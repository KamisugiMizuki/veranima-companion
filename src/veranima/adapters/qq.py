"""OneBot v11（NapCatQQ）适配器：私聊 1v1 + 白名单 + 主动消息 + 离线思考。

形态（DESIGN.md）：仅指定 QQ 帐号与 bot 1v1 私聊；adapter 层做 QQ 号
白名单过滤。接入方式：aiocqhttp 反向 WebSocket（bot 监听 ws://host:port/ws，
NapCatQQ 作为 WS 客户端连接），不上 nonebot2 全家桶。

- 用户消息：串行处理（asyncio.Lock），agent.handle 同步阻塞调用放线程池
- 主动消息（问候/节庆）：后台线程 tick agent.tick_proactive() → 事件循环发送
- 8.7.4 离线思考：静默 N 分钟后低概率发一条"迟来的回应"（仅 QQ 形态）
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import random
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import unquote, unquote_plus, urlparse

import httpx

from aiocqhttp import CQHttp, Event, Message

from ..core.agent import Agent
from ..core.image_payload import ImagePayloadError, make_image_payload, payload_from_data_url
from ..core.render import render_im
from ..core.qq_advisor import QQProactiveAdvisor
from ..core.qq_proactive import QQProactiveState

logger = logging.getLogger(__name__)
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_DEFAULT_QQ_IMAGE_HOSTS = ("multimedia.nt.qq.com", "multimedia.nt.qq.com.cn")


class OfflineThinkTimer:
    """8.7.4 离线思考定时器：静默 N 分钟后低概率触发（窗口去重）。

    纯判定逻辑（now 注入），便于测试。触发一次后，需再静默满一个窗口
    才可能再次触发（防止 bot 自说自话刷屏）。
    """

    def __init__(
        self,
        silence_minutes: int = 30,
        probability: float = 0.3,
        max_per_day: int = 2,
        growth_factor: float = 0.08,
        max_probability: float = 0.95,
        rand: random.Random | None = None,
    ):
        self.silence_minutes = max(1, int(silence_minutes))
        self.probability = max(0.0, min(1.0, probability))  # 当前概率（miss 后增长）
        self._base_probability = self.probability           # 基础概率（发送后重置回）
        self.growth_factor = max(0.0, min(1.0, growth_factor))
        self.max_probability = max(0.0, min(1.0, max_probability))
        self.max_per_day = max(0, int(max_per_day))  # 0 = 不限
        self._rand = rand or random.Random()
        self._last_check_at: float | None = None  # 上次掷骰时间（窗口内只掷一次）
        self._day: str | None = None         # 当前计数日（YYYY-MM-DD）
        self._day_count: int = 0             # 当日已触发次数

    def due(self, now: float, last_activity: float | None) -> bool:
        """静默超过 N 分钟且本窗口未触发 → 掷骰决定是否触发。

        每个静默窗口只掷一次骰（`_last_check_at` 窗口去重），
        否则 60s tick 会在窗口内掷 30 次骰、概率闸门形同虚设
        （2026-08-04 修复：0.3 概率实际 ≈100% 每 30 分钟必发）。

        渴望度积累（借鉴 revive-companion 的 PoissonEngine）：
        掷骰未命中 → 概率 +growth_factor（"想念"随时间积累）；
        命中 → 概率重置回基础值（"想念"得到满足）。
        概率为 0 时视为关闭（永不触发，也不增长）。

        每日上限：同一天触发次数达到 max_per_day 后不再触发
        （2026-08 修复：防止用户入睡后整夜反复轰炸）。
        """
        if last_activity is None:
            return False
        if now - last_activity < self.silence_minutes * 60:
            return False
        # 窗口内只判定一次（无论是否触发，满一个窗口才重新掷骰）
        if self._last_check_at is not None and now - self._last_check_at < self.silence_minutes * 60:
            return False
        self._last_check_at = now
        if self.max_per_day > 0:
            day = self._day_of(now)
            if day != self._day:
                self._day = day
                self._day_count = 0
            if self._day_count >= self.max_per_day:
                return False  # 当日额度已用完
        if self._rand.random() >= self.probability:
            self._grow()  # miss → 渴望度积累
            return False
        # 命中：概率重置（发送后"想念满足"）
        self.probability = self._base_probability
        if self.max_per_day > 0:
            self._day_count += 1
        return True

    def _grow(self) -> None:
        """渴望度积累：未命中时概率增长，封顶 max_probability。"""
        if self.probability <= 0:
            return  # 0 = 关闭（永不触发）
        self.probability = min(self.max_probability, self.probability + self.growth_factor)

    @staticmethod
    def _day_of(now: float) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")


class QQAdapter:
    """OneBot v11 反向 WebSocket 适配器（NapCatQQ 连入 ws://host:port/ws）。"""

    def __init__(
        self,
        agent: Agent,
        *,
        ws_host: str = "127.0.0.1",
        ws_port: int = 8099,
        access_token: str = "",
        allowed_qq: list[str] | list[int] | tuple = (),
        proactive: bool = True,
        offline_think: OfflineThinkTimer | None = None,
        tick_interval: float = 60.0,
        quiet_hours: tuple[int, int] | None = (23, 8),
        proactive_delay_minutes: int = 5,
        sticker_library=None,  # StickerLibrary | None；None = 表情包功能关闭
        agent_lock: asyncio.Lock | None = None,
        image_roots: list[str | Path] | None = None,
        max_image_bytes: int = 10 * 1024 * 1024,
        trusted_image_proxy: bool = False,
        image_proxy_hosts: list[str] | tuple[str, ...] = (),
    ):
        self.agent = agent
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.allowed = {str(q) for q in allowed_qq}
        self.proactive = proactive
        self.offline = offline_think or OfflineThinkTimer()
        self._tick_interval = tick_interval
        self.quiet_hours = quiet_hours  # (开始小时, 结束小时)；None = 不限制
        self.proactive_delay_minutes = max(1, int(proactive_delay_minutes))
        qq_proactive_cfg = ((getattr(agent, "config", {}) or {}).get("proactive", {}) or {}).get("channels", {}).get("qq", {})
        self.qq_advisor = QQProactiveAdvisor(agent.memory, config=qq_proactive_cfg)
        self._qq_evaluation_at: float = 0.0
        self._qq_opportunity: dict | None = None
        self.stickers = sticker_library  # 8.6.3 表情包库（None = 关闭）
        self._lock = agent_lock or asyncio.Lock()
        from ..config import ROOT
        roots = image_roots or [ROOT / "data", ROOT]
        self.image_roots = tuple(Path(p).resolve() for p in roots)
        self.max_image_bytes = max(1024, int(max_image_bytes))
        self.trusted_image_proxy = bool(trusted_image_proxy)
        proxy_hosts = image_proxy_hosts or _DEFAULT_QQ_IMAGE_HOSTS
        self.image_proxy_hosts = tuple(
            str(host).strip().lower().rstrip(".") for host in proxy_hosts if str(host).strip()
        )
        self._sticker_tasks: set[asyncio.Task] = set()
        self._last_user_activity: float | None = None
        self._pending_proactive: str | None = None  # 延迟主动消息（对话静默后发送）
        self.bot = CQHttp(access_token=access_token or None, message_class=Message)
        self._register()

    # ---------- 消息处理 ----------

    def _register(self) -> None:
        @self.bot.on_message("private")
        async def _on_private(event: Event) -> None:
            await self._handle_private(event)

    async def _handle_private(self, event: Event) -> None:
        """私聊消息：白名单过滤 → 文本/图片提取 → 串行 handle → 回复（主动消息延迟）。"""
        uid = str(event.get("user_id", ""))
        if uid not in self.allowed:
            logger.info("ignored private message from non-whitelist qq=%s", uid)
            return
        text = self._plain_text(event)
        self.qq_advisor.note_user_message(text)
        # 8.6.2/8.6.3：图片段 → 下载 (data_url, raw_bytes)；下载失败降级，不阻塞对话
        images = await self._collect_images(event)
        if not text and not images:
            logger.info("no text/image in message from qq=%s, skipped", uid)
            return
        logger.info("qq=%s >> %s (images=%d)", uid, text[:80], len(images))
        # 用户又说话了 → 对话恢复，旧的延迟主动消息作废（不插话）
        if self._pending_proactive:
            logger.info("pending proactive discarded (user active again): %s", self._pending_proactive[:60])
            self._pending_proactive = None
        # 串行处理：agent 内部状态（历史/记忆/学习）有顺序依赖，禁止并发写
        async with self._lock:
            # R4 通道互斥（DESIGN 5.4）：QQ 消息到达 → 记 QQ 通道活跃（桌宠感知降功耗）
            try:
                self.agent.activity.touch("qq")
            except Exception:
                pass
            # R4_SPEC 4 忽略自愈：用户来消息 → 最近主动反馈标记 responded + 重置忽略
            try:
                fb = self.agent.memory.recent_proactive_feedback(channel="qq", limit=3)
                pending = [f for f in fb if not f["responded"]]
                if pending:
                    src = pending[-1]["source"]
                    self.agent.memory.record_proactive_feedback(source=src, channel="qq", responded=True)
                    self.agent.gate.note_responded(src, channel="qq")
            except Exception:
                pass
            result = await asyncio.to_thread(self.agent.handle, text, [d for d, _ in images], channel="im")
        self._last_user_activity = time.time()
        if result.reply:
            # 统一 IM 出口：Reply 也必须经过 render_im，不能绕过波浪号/感叹号/
            # emoji 规则直接发送原始 LLM 文本。
            rendered = render_im(result.reply_obj or result.reply, self.agent.state)
            if rendered:
                await self.bot.send(event, rendered)
            logger.info("qq=%s << %s", uid, result.reply[:80])
            # 8.6.3 表情包输出：回复后按情绪宽松匹配，附加一张（不刷屏）
            sticker = self._pick_sticker_for_reply(result.reply)
            if sticker:
                await self.bot.send(event, f"[CQ:image,file={sticker}]")
                logger.info("qq=%s << (表情包) %s", uid, sticker)
        if result.proactive_msg:
            # 不立即发送：等对话静默满 proactive_delay_minutes 后由后台 tick 发送，
            # 避免"回复 + 主动"双连发（2026-08-04 修复：真人不会秒补一句无关话题）
            self._pending_proactive = result.proactive_msg
            logger.info(
                "qq=%s << (主动-pending, %dmin 后发) %s",
                uid, self.proactive_delay_minutes, result.proactive_msg[:60],
            )
        if images:
            self._schedule_sticker_ingest(images)

    def _schedule_sticker_ingest(self, images: list[tuple[str, bytes]]) -> None:
        """回复发送后再标注；复用 Agent 锁，避免与对话 LLM 并发。"""
        if self.stickers is None:
            return

        async def run() -> None:
            async with self._lock:
                await asyncio.to_thread(self._ingest_stickers, images)

        task = asyncio.create_task(run())
        self._sticker_tasks.add(task)

        def done(finished: asyncio.Task) -> None:
            self._sticker_tasks.discard(finished)
            if not finished.cancelled() and finished.exception():
                logger.warning("sticker background ingest failed: %s", finished.exception())

        task.add_done_callback(done)

    def _ingest_stickers(self, images: list[tuple[str, bytes]] | list[bytes]) -> None:
        """8.6.3 表情包入库：没见过的 → LLM 标注 → 存库。

        判重用 dHash（同图不同尺寸/压缩也识别）；标注失败不强行入库。
        """
        if self.stickers is None:
            return
        for item in images:
            try:
                raw = item[1] if isinstance(item, tuple) else item
                if self.stickers.find_similar(raw):
                    continue  # 见过，不重复入库
                if isinstance(item, tuple):
                    data_url, raw = item
                    payload = self._payload_from_download_result(data_url, raw)
                else:
                    raw = item
                    payload = make_image_payload(raw, source="qq")
                    data_url = payload.data_url
                if payload.animated:
                    continue  # 动图只给当前轮 LLM，不进入长期表情库
                meta = self.agent.annotate_sticker(payload.data_url)
                if meta and meta.get("is_sticker") is True:
                    meta = {k: v for k, v in meta.items() if k in ("meaning", "moods", "scenarios")}
                    self.stickers.add_payload(payload, **meta)
            except Exception as e:
                logger.debug("sticker ingest failed: %s", e)

    @staticmethod
    def _payload_from_download_result(data_url: str, raw: bytes):
        """Revalidate downloader output at the adapter boundary."""
        ctype = str(data_url).split(";", 1)[0].removeprefix("data:")
        return make_image_payload(raw, content_type=ctype, source="qq:http")

    def _pick_sticker_for_reply(self, reply: str) -> str | None:
        """8.6.3 表情包输出：按回复情绪宽松匹配，返回本地文件路径或 None。

        约束：情绪命中才发（宽松：相近即可）；低使用次数优先。
        """
        if self.stickers is None or len(self.stickers) == 0:
            return None
        mood = self._reply_mood(reply)
        scenario = reply[:120]
        if not mood and not scenario:
            return None
        finder = getattr(self.stickers, "find_for_context", None)
        cands = finder(mood or "", scenario, limit=3) if finder else self.stickers.find_for_mood(mood, limit=3)
        if not cands:
            return None
        entry = cands[0]
        self.stickers.record_use(entry)
        path_for = getattr(self.stickers, "path_for", None)
        return str(path_for(entry) if path_for else self.stickers.root / entry.file)

    @staticmethod
    def _reply_mood(reply: str) -> str | None:
        """回复文本 → 情绪标签（宽松匹配用）。无情绪 → None（不发）。"""
        from ..core.agent import Agent
        emo = Agent._detect_emotion(reply)
        mapping = {
            "很开心": "开心",
            "有点低落": "难过",
        }
        return mapping.get(emo)

    @staticmethod
    def _plain_text(event: Event) -> str:
        """提取文本并保留原生 QQ face 的语义占位。"""
        def extract(value) -> str:
            if isinstance(value, (list, tuple)):
                try:
                    value = Message(value)
                except Exception:
                    return ""
            if isinstance(value, Message):
                parts = []
                has_text = any(seg.get("type") == "text" for seg in value)
                for seg in value:
                    data = seg.get("data") or {}
                    if seg.get("type") == "text":
                        piece = str(data.get("text") or "")
                        parts.append(re.sub(
                            r"\[表情\s*\[([^\]]+)\]\s*\]",
                            lambda m: f"[QQ表情：{m.group(1).strip()}]",
                            piece,
                        ))
                    elif seg.get("type") == "face":
                        summary = str(
                            data.get("summary") or data.get("text") or data.get("raw") or ""
                        ).strip().strip("[]")
                        face_id = str(data.get("id") or "").strip()
                        if not summary and not data.get("summary") and has_text:
                            parts.append(f"[QQ表情：id={face_id}]" if face_id else "[QQ表情：未知]")
                            continue
                        summary = summary or (f"id={face_id}" if face_id else "未知")
                        parts.append(f"[QQ表情：{summary}]")
                return "".join(parts).strip()
            raw = str(value or "")
            if "[CQ:face," in raw:
                try:
                    return extract(Message(raw))
                except Exception:
                    pass
            raw = re.sub(r"\[CQ:(?:image|file),[^\]]*\]", "", raw)
            return re.sub(
                r"\[表情\s*\[([^\]]+)\]\s*\]",
                lambda m: f"[QQ表情：{m.group(1).strip()}]",
                raw,
            ).strip()

        text = extract(event.get("message", ""))
        if not text or text in {"[图片]", "[文件]", "[表情]"}:
            fallback = extract(event.get("raw_message", ""))
            if fallback and fallback not in {"[图片]", "[文件]", "[表情]"}:
                text = fallback
        if text in {"[图片]", "[文件]", "[表情]"}:
            return ""
        return text

    async def _collect_images(self, event: Event) -> list[tuple[str, bytes]]:
        """8.6.2 图像输入：提取消息中的图片段并下载。

        返回 [(data_url, raw_bytes), ...]；下载失败/无图片段返回 []。
        data_url 供 LLM 多模态看图；raw_bytes 供 8.6.3 表情包入库。
        """
        segments = self._image_segments(event.get("message", ""))
        if not segments:
            segments = self._image_segments(event.get("raw_message", ""))
        if segments:
            logger.info("qq image segments received: count=%d", len(segments))
        if not segments:
            return []
        if len(segments) > 4:
            logger.warning("qq image limit: dropped %d images", len(segments) - 4)
            segments = segments[:4]
        results = await asyncio.gather(*(self._payload_from_segment(data) for data in segments))
        ok = [r for r in results if r]
        if len(ok) < len(segments):
            logger.warning("image resolve failed: %d/%d images dropped", len(segments) - len(ok), len(segments))
        return [(payload.data_url, payload.raw) for payload in ok]

    @staticmethod
    def _image_segments(msg) -> list[dict]:
        """Accept OneBot segment arrays and CQ image strings from NapCat."""
        if isinstance(msg, Message):
            values = list(msg)
        elif isinstance(msg, (list, tuple)):
            values = list(msg)
        else:
            values = []
            for raw in re.findall(r"\[CQ:image,([^\]]+)\]", str(msg or "")):
                data = {}
                for item in raw.split(","):
                    key, sep, value = item.partition("=")
                    if sep:
                        data[key] = unquote_plus(value)
                values.append({"type": "image", "data": data})
        result = []
        for seg in values:
            if not isinstance(seg, dict) or seg.get("type") not in {"image", "file"}:
                continue
            data = seg.get("data") or {}
            name = str(data.get("file") or data.get("name") or data.get("path") or "").lower()
            mime = str(data.get("type") or data.get("mime") or "").lower()
            if seg.get("type") == "image" or mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                result.append(data)
        return result

    async def _payload_from_segment(self, data: dict, *, _allow_lookup: bool = True):
        """Resolve OneBot image data: data URL, HTTP URL, local file/path, get_image API."""
        refs = [data.get("url"), data.get("path"), data.get("file")]
        for ref in refs:
            if not ref:
                continue
            ref = str(ref)
            try:
                if ref.startswith("data:"):
                    return payload_from_data_url(ref, source="qq:data")
                if ref.startswith(("http://", "https://")):
                    result = await asyncio.to_thread(self._download_image, ref)
                    if result:
                        return self._payload_from_download_result(result[0], result[1])
                local = self._resolve_local_image(ref)
                if local:
                    return make_image_payload(local.read_bytes(), source="qq:file")
            except (OSError, ImagePayloadError):
                continue
        # NapCat may expose only a file id; ask OneBot for the actual path/URL.
        file_ref = data.get("file") or data.get("path")
        call_action = getattr(self.bot, "call_action", None)
        if _allow_lookup and file_ref and callable(call_action):
            try:
                result = await call_action("get_image", file=str(file_ref))
                info = (result or {}).get("data", result or {})
                if isinstance(info, dict):
                    return await self._payload_from_segment({
                        "url": info.get("url"), "path": info.get("path"), "file": info.get("file")
                    }, _allow_lookup=False)
            except Exception as exc:
                logger.debug("OneBot get_image failed: %s", exc)
        return None

    def _resolve_local_image(self, value: str) -> Path | None:
        """Resolve an existing local image without accepting traversal under configured roots."""
        raw = unquote(str(value))
        if raw.startswith("file://"):
            raw = urlparse(raw).path
        candidates = []
        path = Path(raw)
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend(root / raw for root in self.image_roots)
            candidates.extend(root / path.name for root in self.image_roots)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if not resolved.is_file():
                    continue
                if not any(resolved == root or resolved.is_relative_to(root) for root in self.image_roots):
                    continue
                if resolved.stat().st_size > self.max_image_bytes:
                    continue
                if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    continue
                return resolved
            except OSError:
                continue
        return None

    def _pinned_http_url(self, url: str) -> tuple[str, str, str] | None:
        """Resolve once; only an explicit QQ-CDN fake-IP exception may skip IP pinning."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                return None
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
            try:
                addresses = [ipaddress.ip_address(hostname)]
            except ValueError:
                addresses = [
                    ipaddress.ip_address(info[4][0].split("%", 1)[0])
                    for info in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
                ]
            default_port = 443 if parsed.scheme == "https" else 80
            host_header = hostname if port == default_port else f"{hostname}:{port}"
            if addresses and all(address.is_global for address in addresses):
                address = sorted(set(addresses), key=lambda item: (item.version, item.packed))[0]
                ip_host = f"[{address}]" if address.version == 6 else str(address)
                return parsed._replace(netloc=f"{ip_host}:{port}").geturl(), hostname, host_header
            proxy_host_allowed = any(
                hostname == allowed or hostname.endswith(f".{allowed}")
                for allowed in self.image_proxy_hosts
            )
            if (
                self.trusted_image_proxy and proxy_host_allowed and addresses
                and all(address in _FAKE_IP_NETWORK for address in addresses)
            ):
                return parsed._replace(netloc=f"{hostname}:{port}").geturl(), hostname, host_header
            return None
        except (OSError, UnicodeError, ValueError):
            return None

    def _download_image(self, url: str) -> tuple[str, bytes] | None:
        """下载图片为 (data_url, raw_bytes)。连接固定到校验过的公网 IP。"""
        pinned = self._pinned_http_url(url)
        if not pinned:
            logger.warning("blocked unsafe image URL: %s", url[:80])
            return None
        pinned_url, sni_hostname, host_header = pinned
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                with client.stream(
                    "GET", pinned_url, headers={"Host": host_header},
                    extensions={"sni_hostname": sni_hostname},
                ) as resp:
                    resp.raise_for_status()
                    size = int(resp.headers.get("content-length") or 0)
                    if size > self.max_image_bytes:
                        return None
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_image_bytes:
                            return None
            payload = make_image_payload(
                bytes(body),
                content_type=resp.headers.get("content-type", "").split(";", 1)[0],
                source="qq:http",
            )
            return payload.data_url, payload.raw
        except Exception as e:
            logger.debug("image download failed (%s): %s", url[:80], e)
            return None

    # ---------- 运行 ----------

    def run(self) -> None:
        """启动：后台主动线程 + OneBot WS 服务（阻塞）。"""
        stop = threading.Event()
        t = threading.Thread(
            target=self._bg_loop, args=(stop,), daemon=True, name="veranima-qq-bg"
        )
        t.start()
        try:
            self.bot.run(host=self.ws_host, port=self.ws_port)
        finally:
            stop.set()

    async def run_task(self) -> None:
        """与桌宠核心共用事件循环/Agent 的异步入口。"""
        stop = asyncio.Event()
        background = asyncio.create_task(self._bg_loop_async(stop))
        try:
            await self.bot.run_task(host=self.ws_host, port=self.ws_port)
        finally:
            stop.set()
            background.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await background
            tasks = list(self._sticker_tasks)
            if tasks:
                workers = asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.shield(workers)
                except asyncio.CancelledError:
                    await workers
                    raise

    async def _bg_loop_async(self, stop: asyncio.Event) -> None:
        """同进程模式的主动循环；所有 Agent 调用共享 self._lock。"""
        while not stop.is_set():
            try:
                if not self._in_quiet_hours():
                    if self.proactive:
                        await self._evaluate_qq_opportunity_async()
                        await self._flush_pending_proactive_async()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("async bg proactive tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._tick_interval)
            except asyncio.TimeoutError:
                pass

    async def _flush_pending_proactive_async(self) -> None:
        if not self._pending_proactive or self._last_user_activity is None:
            return
        if time.time() - self._last_user_activity < self.proactive_delay_minutes * 60:
            return
        msg, self._pending_proactive = self._pending_proactive, None
        candidate = self._qq_candidate_from_text(msg, source="scene")
        if not self.agent.gate.decide(
            candidate, scene=self.agent.scene_lock.current(),
        ).allow:
            logger.info("pending qq proactive suppressed by gate")
            return
        text = render_im(msg, self.agent.state)
        if not text:
            return
        if await self._send_to_all_async(text):
            self.agent.record_proactive_message(text, channel="qq")
            self.agent.gate.commit(candidate)
            self.agent.memory.record_proactive_feedback(source=candidate.source, channel="qq")
        else:
            self._pending_proactive = msg

    async def _evaluate_qq_opportunity_async(self) -> None:
        """QQ-only readiness evaluation; never consumes desktop visual events."""
        now = time.time()
        interval = int(self.qq_advisor.engine.config.get("evaluation_interval_minutes", 15))
        if now < self._qq_evaluation_at:
            return
        self._qq_evaluation_at = now + max(1, interval) * 60
        import datetime
        current = datetime.datetime.now().astimezone()
        self.qq_advisor.refresh_state()
        if not self.qq_advisor.state.last_user_message_at:
            logger.info("qq proactive skipped: no QQ user history")
            return
        if not self.qq_advisor.engine.state_allows_proactive(self.qq_advisor.state, current):
            logger.info("qq proactive suppressed: user state=%s", self.qq_advisor.state.user_state)
            return
        readiness, material = self.qq_advisor.evaluate(current, query=self.qq_advisor.last_user_text())
        schedule = self.qq_advisor.engine.schedule(readiness.score)
        logger.info("qq proactive evaluation score=%.3f material=%s action=%s",
                    readiness.score, material.kind, schedule.action)
        if schedule.action != "generate":
            self._qq_evaluation_at = now + schedule.delay_minutes * 60
            return
        candidate = self._qq_candidate(material)
        decision = self.agent.gate.decide(
            candidate, scene=self.agent.scene_lock.current(),
        )
        if not decision.allow:
            logger.info("qq proactive suppressed by gate: %s", decision.reason)
            return
        async with self._lock:
            text = await asyncio.to_thread(self._generate_qq_proactive, material, current)
        if not text:
            self.agent.gate.note_failure(candidate)
            return
        self._qq_opportunity = {"candidate": candidate, "text": text, "created_at": now}
        await self._send_qq_opportunity_async()

    @staticmethod
    def _qq_candidate(material):
        from ..core.ambient import ProactiveCandidate
        source = "shared_episode" if material.kind == "memory" else "scene"
        return ProactiveCandidate(
            channel="qq", source=source, reason=f"QQ 主动素材：{material.kind}",
            relevance=max(0.65, material.confidence), urgency=0.4, intent="check_in",
            context={"material": material.text, "source_id": material.source_id},
        )

    @staticmethod
    def _qq_candidate_from_text(text: str, source: str = "scene"):
        from ..core.ambient import ProactiveCandidate
        return ProactiveCandidate(
            channel="qq", source=source, reason="延迟主动消息", relevance=0.7,
            urgency=0.3, intent="check_in", context={"text": text[:240]},
        )

    def _generate_qq_proactive(self, material, now) -> str:
        elapsed = ""
        last_at = self.qq_advisor.state.last_user_message_at
        if last_at:
            then = self.qq_advisor.engine._parse(last_at)
            elapsed = f"距上次 QQ 对话约 {(now - then).total_seconds() / 3600:.1f} 小时。"
        prefix = self.qq_advisor.engine.virtual_state_prefix(
            (now - then).total_seconds() / 3600 if last_at else 72
        )
        task = (
            "只为 QQ 文字聊天生成一条主动消息。QQ 是异步、高打扰媒介，消息要短、克制、可回可不回。"
            f"{prefix}{elapsed}素材类型={material.kind}，素材={material.text[:240] or '无具体素材'}。"
            "优先跟进真实素材；没有可靠素材时只发低负担存在确认。"
            "不要编造后台活动、外部事实或用户没说过的回忆；不要输出解释、JSON或时间协议前缀。"
        )
        return str(self.agent._short_task(task, max_tokens=1024) or "").strip()

    async def _send_qq_opportunity_async(self) -> None:
        opportunity, self._qq_opportunity = self._qq_opportunity, None
        if not opportunity:
            return
        candidate = opportunity["candidate"]
        text = render_im(opportunity["text"], self.agent.state)
        if not text:
            self.agent.gate.note_failure(candidate)
            return
        if not await self._send_to_all_async(text):
            self.agent.gate.note_failure(candidate)
            return
        self.agent.record_proactive_message(text, channel="qq")
        self.agent.gate.commit(candidate)
        self.agent.memory.record_proactive_feedback(
            source=candidate.source, channel="qq",
            candidate_id=f"qq-{int(opportunity['created_at'] * 1000)}",
        )

    async def _tick_offline_think_async(self) -> None:
        if not self.offline.due(time.time(), self._last_user_activity):
            return
        model_check = getattr(self.agent.llm, "is_model_loaded", None)
        if model_check is not None and not model_check():
            return
        async with self._lock:
            msg = await asyncio.to_thread(self.agent.late_reply)
            if not msg:
                msg = await asyncio.to_thread(self.agent.heartbeat)
        if msg:
            await self._send_to_all_async(msg)

    async def _send_to_all_async(self, msg: str) -> bool:
        try:
            card = self.agent.card
            emoji_freq = (card.veranima or {}).get("emoji_frequency", "low") if card else "low"
            attachment = self.agent.state.attachment
        except Exception:
            emoji_freq, attachment = "low", 0.5
        msg = render_im(msg, attachment=attachment, emoji_frequency=emoji_freq)
        if not msg:
            return False
        for uid in self.allowed:
            await self.bot.send_private_msg(user_id=int(uid), message=msg)
        return True

    def _bg_loop(self, stop: threading.Event) -> None:
        """后台线程：等待事件循环就绪后，周期性执行问候/节庆/离线思考。"""
        loop = None
        deadline = time.time() + 30
        while loop is None and time.time() < deadline and not stop.is_set():
            loop = getattr(self.bot, "_loop", None)
            if loop is None:
                stop.wait(0.2)
        if loop is None:
            logger.warning("bot event loop not ready in 30s, proactive disabled")
            return
        while not stop.is_set():
            try:
                if self._in_quiet_hours():
                    # 静默时段（如 23:00-08:00）：问候/节庆/离线思考一律不主动发
                    logger.debug("in quiet hours, proactive tick skipped")
                else:
                    if self.proactive:
                        self._flush_pending_proactive(loop)
                        self._evaluate_qq_opportunity(loop)
            except Exception:
                logger.exception("bg proactive tick failed")
            stop.wait(self._tick_interval)

    def _flush_pending_proactive(self, loop: asyncio.AbstractEventLoop) -> None:
        """standalone QQ 线程入口：复用 async pending 发送与通道 Gate。"""
        asyncio.run_coroutine_threadsafe(self._flush_pending_proactive_async(), loop)

    def _evaluate_qq_opportunity(self, loop: asyncio.AbstractEventLoop) -> None:
        """Standalone QQ entry: schedule the same async QQ evaluator on its bot loop."""
        asyncio.run_coroutine_threadsafe(self._evaluate_qq_opportunity_async(), loop)

    def _in_quiet_hours(self, now: datetime.datetime | None = None) -> bool:
        """静默时段判定：(开始小时, 结束小时)，支持跨午夜（如 23:00-08:00）。"""
        if not self.quiet_hours:
            return False
        import datetime
        now = now or datetime.datetime.now()
        start, end = self.quiet_hours
        if start < end:
            return start <= now.hour < end
        return now.hour >= start or now.hour < end  # 跨午夜

    def _tick_offline_think(self, loop: asyncio.AbstractEventLoop) -> None:
        """R4 离线思考 + 心跳：静默窗口命中 → late_reply 优先 → heartbeat 兜底（R4_SPEC 1）。"""
        if self._in_quiet_hours():
            return
        if not self.offline.due(time.time(), self._last_user_activity):
            return
        model_check = getattr(self.agent.llm, "is_model_loaded", None)
        if model_check is not None and not model_check():
            logger.info("offline think skipped: model not loaded")
            return
        msg = self.agent.late_reply()
        if not msg:
            # 对话已闭合（late_reply 不触发）→ 心跳破冰
            msg = self.agent.heartbeat()
        if msg:
            logger.info("offline think reply: %s", msg[:60])
            # R4_SPEC 4：离线主动反馈记录（用户回应由 on_message 标记）
            self._send_to_all(loop, msg)

    def _send_to_all(self, loop: asyncio.AbstractEventLoop, msg: str) -> None:
        """后台线程 → bot 事件循环，向白名单 QQ 号发送私聊消息。

        发送前过 IM 通道渲染器（DESIGN 4.8：感叹号限频/波浪号阈值/换行压缩/表情限频）。
        """
        try:
            card = self.agent.card
            emoji_freq = (card.veranima or {}).get("emoji_frequency", "low") if card else "low"
            attachment = self.agent.state.attachment
        except Exception:
            emoji_freq, attachment = "low", 0.5
        msg = render_im(msg, attachment=attachment, emoji_frequency=emoji_freq)
        if not msg:
            return
        for uid in self.allowed:
            fut = asyncio.run_coroutine_threadsafe(
                self.bot.send_private_msg(user_id=int(uid), message=msg), loop
            )
            try:
                fut.result(timeout=15)
                logger.info("proactive msg sent to qq=%s: %s", uid, msg[:60])
            except Exception:
                logger.exception("failed to send proactive msg to qq=%s", uid)

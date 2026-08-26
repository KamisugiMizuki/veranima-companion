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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, unquote_plus, urlparse

import httpx

from aiocqhttp import CQHttp, Event, Message

from ..core.agent import Agent
from ..core.image_payload import ImagePayloadError, make_image_payload, payload_from_data_url
from ..core.render import render_im
from ..core.stickers import build_sticker_query
from ..core.qq_advisor import QQProactiveAdvisor
from ..core.qq_proactive import QQProactiveState
from ..core.proactive import MealReminderScheduler
from ..core.tension_events import extract_direct_question

logger = logging.getLogger(__name__)
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_DEFAULT_QQ_IMAGE_HOSTS = ("multimedia.nt.qq.com", "multimedia.nt.qq.com.cn")
_UNANCHORED_PROACTIVE = re.compile(
    r"(?:之前|上次)?(?:那|这)(?:件)?事|(?:之前|上次)(?:那个|说的(?:那个|那件)?)"
)
_ANCHOR_STOPWORDS = {
    "用户", "今天", "昨天", "明天", "刚刚", "之前", "上次", "继续", "然后",
    "后来", "现在", "最近", "那个", "这件", "事情", "问题", "进度", "一下",
    "说过", "提过", "想起", "跟进", "回复", "消息", "对话", "学", "做", "看",
    "吃", "聊", "说", "去", "要", "想", "把", "了", "的", "在", "很", "又",
    "还", "和", "与", "跟", "是", "有", "没", "未", "存在", "超过", "一小时",
}


def _proactive_anchor_terms(material: str) -> list[str]:
    value = str(material or "")
    for stopword in sorted(_ANCHOR_STOPWORDS, key=len, reverse=True):
        value = value.replace(stopword, " ")
    terms: list[str] = []
    for chunk in re.findall(r"[一-龥]{2,}|[A-Za-z0-9]{2,}", value):
        if len(chunk) <= 4:
            terms.append(chunk)
            continue
        terms.append(chunk)
        terms.extend(chunk[i:i + size] for size in (2, 3, 4) for i in range(len(chunk) - size + 1))
    return list(dict.fromkeys(term for term in terms if term not in _ANCHOR_STOPWORDS))

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
        sticker_learning_mode: str = "review",
        sticker_send_rate: str = "normal",
        sticker_min_reply_gap: int = 3,
        sticker_rand: random.Random | None = None,
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
        meal_cfg = ((getattr(agent, "config", {}) or {}).get("proactive", {}) or {}).get("meal_reminders", {})
        self.meal_scheduler = MealReminderScheduler(meal_cfg)
        self._meal_send_inflight = False
        self._qq_evaluation_at: float = 0.0
        self._qq_opportunity: dict | None = None
        self.stickers = sticker_library  # 8.6.3 表情包库（None = 关闭）
        self.sticker_learning_mode = (
            sticker_learning_mode if sticker_learning_mode in {"off", "review", "auto"} else "review"
        )
        self.sticker_send_rate = (
            sticker_send_rate
            if sticker_send_rate in {"off", "low", "normal", "frequent", "always"}
            else "normal"
        )
        self.sticker_min_reply_gap = max(1, int(sticker_min_reply_gap))
        self._sticker_rand = sticker_rand or random.Random()
        self._sticker_reply_count = 0
        self._last_sticker_reply_count: int | None = None
        self._recent_sticker_ids: deque[str] = deque(maxlen=5)
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
        # HERMES_AGENT_INTEGRATION_SPEC 阶段 4：QQ 任务入口（tasks 未启用时 bridge=None，零开销）
        from ..core.task_session import QQTaskSessionManager
        from ..tools.hermes_bridge import HermesBridgeError, HermesExecutionBridge, load_bridge_config
        tasks_cfg = (getattr(agent, "config", {}) or {}).get("tasks", {}) or {}
        bridge_cfg = load_bridge_config(tasks_cfg)
        self.task_bridge = HermesExecutionBridge(bridge_cfg) if bridge_cfg.get("enabled") else None
        self.tasks = QQTaskSessionManager(agent, self.task_bridge)
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
        # 阶段 4 任务分流：命中任务动作时不进陪伴对话链
        task_action = self.tasks.route(uid, text)
        if task_action is not None:
            await self._handle_task_action(uid, task_action, event)
            return
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
            previous_scope = getattr(self.agent, "_current_user_scope", None)
            self.agent._current_user_scope = f"qq:{uid}"
            try:
                result = await asyncio.to_thread(
                    self.agent.handle,
                    text,
                    [item[0] for item in images],
                    channel="im",
                )
            finally:
                self.agent._current_user_scope = previous_scope
                runtime = getattr(self.agent, "schedule_runtime", None)
                if runtime is not None and not runtime.sleeping:
                    runtime.resume_activity(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self._last_user_activity = time.time()
        if result.reply:
            # 统一 IM 出口：Reply 也必须经过 render_im，不能绕过波浪号/感叹号/
            # emoji 规则直接发送原始 LLM 文本。
            rendered = render_im(result.reply_obj or result.reply, self.agent.state)
            if rendered:
                await self.bot.send(event, rendered)
                self._sticker_reply_count += 1
                entry = self._pick_sticker_for_reply(result, rendered, text, uid)
                if entry is not None:
                    try:
                        sticker_path = self.stickers.path_for(entry)
                        await self.bot.send(event, f"[CQ:image,file={sticker_path}]")
                    except Exception as exc:
                        logger.warning("qq sticker send failed: %s", exc)
                    else:
                        self.stickers.record_use(entry)
                        self._recent_sticker_ids.append(entry.id)
                        self._last_sticker_reply_count = self._sticker_reply_count
                        logger.info("qq=%s << sticker=%s", uid, entry.id[:12])
            logger.info("qq=%s << %s", uid, result.reply[:80])
        if result.proactive_msg:
            # 不立即发送：等对话静默满 proactive_delay_minutes 后由后台 tick 发送，
            # 避免"回复 + 主动"双连发（2026-08-04 修复：真人不会秒补一句无关话题）
            self._pending_proactive = result.proactive_msg
            logger.info(
                "qq=%s << (主动-pending, %dmin 后发) %s",
                uid, self.proactive_delay_minutes, result.proactive_msg[:60],
            )
        if images:
            self._schedule_sticker_ingest(images, owner_scope=f"qq:{uid}")

    def _schedule_sticker_ingest(
        self,
        images: list[tuple],
        *,
        owner_scope: str,
    ) -> None:
        """回复发送后再标注；复用 Agent 锁，避免与对话 LLM 并发。"""
        if self.stickers is None or self.sticker_learning_mode == "off":
            return

        async def run() -> None:
            async with self._lock:
                await asyncio.to_thread(
                    self._ingest_stickers,
                    images,
                    owner_scope=owner_scope,
                )

        task = asyncio.create_task(run())
        self._sticker_tasks.add(task)

        def done(finished: asyncio.Task) -> None:
            self._sticker_tasks.discard(finished)
            if not finished.cancelled() and finished.exception():
                logger.warning("sticker background ingest failed: %s", finished.exception())

        task.add_done_callback(done)

    def _ingest_stickers(
        self,
        images: list[tuple] | list[bytes],
        *,
        owner_scope: str = "legacy_global",
    ) -> None:
        """8.6.3 表情包入库：没见过的 → LLM 标注 → 存库。

        判重用 dHash（同图不同尺寸/压缩也识别）；标注失败不强行入库。
        """
        if self.stickers is None:
            return
        for item in images:
            try:
                raw = item[1] if isinstance(item, tuple) else item
                source = item[2] if isinstance(item, tuple) and len(item) >= 3 else {}
                if self.stickers.find_similar(raw, owner_scope=owner_scope):
                    continue  # 见过，不重复入库
                if isinstance(item, tuple):
                    data_url, raw = item[0], item[1]
                    payload = self._payload_from_download_result(data_url, raw)
                else:
                    raw = item
                    payload = make_image_payload(raw, source="qq")
                    data_url = payload.data_url
                if payload.animated:
                    continue  # 动图只给当前轮 LLM，不进入长期表情库
                meta = self.agent.annotate_sticker(payload.data_url)
                if meta and meta.get("is_sticker") is True:
                    meta = {
                        key: value for key, value in meta.items()
                        if key in ("meaning", "moods", "scenario_tags", "scenarios", "confidence")
                    }
                    common = {**meta, "owner_scope": owner_scope, "source": source}
                    if self.sticker_learning_mode == "review":
                        self.stickers.add_candidate(payload, **common)
                    elif (
                        self.sticker_learning_mode == "auto"
                        and float(meta.get("confidence") or 0.0) >= 0.85
                    ):
                        self.stickers.add_payload(payload, consent="auto", **common)
            except Exception as e:
                logger.debug("sticker ingest failed: %s", e)

    @staticmethod
    def _payload_from_download_result(data_url: str, raw: bytes):
        """Revalidate downloader output at the adapter boundary."""
        ctype = str(data_url).split(";", 1)[0].removeprefix("data:")
        return make_image_payload(raw, content_type=ctype, source="qq:http")

    def _pick_sticker_for_reply(self, result, reply: str, user_text: str, uid: str):
        """按正常 QQ 回复选择同作用域表情；找不到就不发。"""
        if self.stickers is None or len(self.stickers) == 0:
            return None
        reply_obj = getattr(result, "reply_obj", None)
        query = build_sticker_query(reply_obj, reply, user_text)
        if query["suppress"]:
            return None
        explicit = bool(query["explicit_request"])
        if not explicit and not (query["moods"] or query["scenario_tags"]):
            return None
        if not explicit and self.sticker_send_rate == "off":
            return None
        if (
            not explicit
            and self._last_sticker_reply_count is not None
            and self._sticker_reply_count - self._last_sticker_reply_count < self.sticker_min_reply_gap
        ):
            return None
        probability = {
            "low": 0.15,
            "normal": 0.30,
            "frequent": 0.60,
            "always": 1.0,
        }.get(self.sticker_send_rate, 0.0)
        if not explicit and self._sticker_rand.random() >= probability:
            return None
        owner_scope = f"qq:{uid}"
        candidates = self.stickers.find_for_query(
            query,
            owner_scope=owner_scope,
            limit=3,
            recent_ids=self._recent_sticker_ids,
        )
        if not candidates:
            candidates = self.stickers.find_for_query(
                query,
                owner_scope=owner_scope,
                limit=3,
            )
        if not candidates and explicit:
            candidates = sorted(
                (
                    entry for entry in self.stickers.list_entries(owner_scope=owner_scope)
                    if entry.status == "active"
                ),
                key=lambda entry: (entry.uses, entry.last_used_at or "", entry.created_at),
            )[:3]
        return candidates[0] if candidates else None

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

    async def _collect_images(self, event: Event) -> list[tuple[str, bytes, dict]]:
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
        source = {
            "channel": "qq",
            "platform_message_id": str(event.get("message_id") or ""),
            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return [(payload.data_url, payload.raw, dict(source)) for payload in ok]

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
                advance = getattr(self.agent, "advance_schedule_async", None)
                if callable(advance):
                    await advance()
                elif getattr(self.agent, "schedule_runtime", None) is not None:
                    self.agent.schedule_runtime.advance(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
                if getattr(self.agent, "schedule_runtime", None) is not None:
                    notice = self.agent.schedule_runtime.pop_notice()
                    if notice:
                        candidate = self.agent.schedule_notice_candidate(notice, "qq")
                        decision = self.agent.gate.decide(
                            candidate, scene=self.agent.scene_lock.current(),
                            character_sleeping=False,
                        ) if candidate else None
                        text = await asyncio.to_thread(self.agent.schedule_notice_text, notice) if decision and decision.allow else ""
                        if text and await self._send_to_all_async(text):
                            self.agent.gate.commit(candidate)
                            self.agent.record_proactive_message(text, channel="qq")
                if not self._in_quiet_hours():
                    if self.proactive:
                        await self._send_due_meal_reminder_async()
                        await self._evaluate_qq_opportunity_async()
                        await self._flush_pending_proactive_async()
                    # MEMORY_BACKEND_EVAL M-C：每日一次夜间整理（内部有当日去重）
                    await asyncio.to_thread(self.agent.maybe_nightly_digest)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("async bg proactive tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._tick_interval)
            except asyncio.TimeoutError:
                pass

    async def _send_due_meal_reminder_async(self, now=None) -> bool:
        if self._meal_send_inflight:
            return False
        self._meal_send_inflight = True
        try:
            return await self._send_due_meal_reminder_once_async(now)
        finally:
            self._meal_send_inflight = False

    async def _send_due_meal_reminder_once_async(self, now=None) -> bool:
        import datetime
        now = now or datetime.datetime.now().astimezone()
        feedback = self.agent.memory.recent_proactive_feedback(source="meal", channel="qq", limit=30)
        sent_ids = {str(row.get("candidate_id") or "") for row in feedback}
        due = self.meal_scheduler.due(now=now, sent_ids=sent_ids)
        if not due:
            return False
        meal, text, candidate_id = due
        candidate = self._qq_candidate_from_text(text, source="ritual")
        candidate = candidate.__class__(
            source="ritual", reason=f"{meal} meal reminder",
            relevance=1.0, urgency=0.6, intent="remind",
            context={"calendar_source": "meal", "meal": meal}, channel="qq",
        )
        if not self.agent.gate.decide(
            candidate, scene=self.agent.scene_lock.current(), now=now.timestamp(),
        ).allow:
            return False
        async with self._lock:
            if not await self._send_to_all_async(text):
                self.agent.gate.note_failure(candidate)
                return False
            self.agent.record_proactive_message(text, channel="qq")
            self.agent.gate.commit(candidate)
            self.agent.memory.record_proactive_feedback(
                source="meal", channel="qq", candidate_id=candidate_id,
            )
            return True

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
            self._record_qq_expectation(candidate, text)
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
        self._expire_qq_expectations(current)
        self._check_qq_abandonment(current)
        if not self.agent.tension.proactive_allowed():
            logger.info("qq proactive suppressed: relational tension pause")
            return
        if (self.agent.tension.high_tension_proactive
                and self.agent.tension.band in {"repair", "high"}
                and self.agent.tension.state.open_event_ids):
            if await self._send_tension_repair_async(current):
                return
        if not self.qq_advisor.state.last_user_message_at:
            logger.info("qq proactive skipped: no QQ user history")
            return
        if not self.qq_advisor.engine.state_allows_proactive(self.qq_advisor.state, current):
            logger.info("qq proactive suppressed: user state=%s", self.qq_advisor.state.user_state)
            return
        readiness, material = self.qq_advisor.evaluate(current, query=self.qq_advisor.last_user_text())
        if self.agent.tension.band in {"cool", "repair", "high"} and material.kind not in {"memory", "time"}:
            logger.info("qq proactive suppressed: tension band=%s low-value material=%s", self.agent.tension.band, material.kind)
            return
        schedule = self.qq_advisor.engine.schedule(readiness.score)
        logger.info("qq proactive evaluation score=%.3f material=%s action=%s",
                    readiness.score, material.kind, schedule.action)
        if schedule.action != "generate":
            self._qq_evaluation_at = now + schedule.delay_minutes * 60
            return
        schedule_candidate = self.agent.schedule_self_share_candidate("qq")
        curiosity_candidates = [
            self.agent.schedule_curiosity_candidate("qq", user_scope=f"qq:{uid}")
            for uid in sorted(self.allowed)
        ]
        curiosity_candidate = next((item for item in curiosity_candidates if item is not None), None)
        choices = [item for item in (schedule_candidate, curiosity_candidate) if item is not None]
        special_candidate = choices[int(current.toordinal()) % len(choices)] if choices else None
        if special_candidate is not None:
            special_feedback_id = self._qq_feedback_id(special_candidate, current)
            if any(row.get("candidate_id") == special_feedback_id
                   for row in self.agent.memory.recent_proactive_feedback(channel="qq", limit=100)):
                special_candidate = None
        if special_candidate is not None:
            special_decision = self.agent.gate.decide(
                special_candidate, scene=self.agent.scene_lock.current(),
            )
            if special_decision.allow:
                source_text = str((special_candidate.context or {}).get("summary") or special_candidate.reason)
                async with self._lock:
                    text = await asyncio.to_thread(
                        self.agent._short_task,
                        f"根据这个有来源的素材，自然发一条低负担消息：{source_text}。不要提内部标签。",
                    )
                owner_scope = str((special_candidate.context or {}).get("owner_scope") or "")
                sent = (
                    await self._send_to_uid_async(owner_scope.split(":", 1)[1], text)
                    if owner_scope.startswith("qq:") else await self._send_to_all_async(text)
                )
                if text and sent:
                    self.agent.gate.commit(special_candidate)
                    self.agent.record_proactive_message(text, channel="qq")
                    self.agent.memory.record_proactive_feedback(
                        source=special_candidate.source, channel="qq",
                        candidate_id=special_feedback_id,
                    )
                    gap_id = (special_candidate.context or {}).get("gap_id")
                    if gap_id:
                        self.agent.memory.mark_user_info_gap_asked(int(gap_id))
                    return
        candidate = self._qq_candidate(material)
        feedback_id = self._qq_feedback_id(candidate, current)
        if any(row.get("candidate_id") == feedback_id
               for row in self.agent.memory.recent_proactive_feedback(channel="qq", limit=100)):
            logger.info("qq proactive suppressed: source already used today (%s)", feedback_id)
            return
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

    def _record_qq_expectation(self, candidate, text: str) -> None:
        """成功发送后才建立期待；通知/状态分享不要求回复。"""
        import datetime
        question = text if extract_direct_question(text) else ""
        requires_reply = bool(question)
        expires = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=self.agent.tension.UNANSWERED_REPLY_WINDOW_HOURS)
        ).isoformat(timespec="seconds") if requires_reply else None
        self.agent.memory.record_proactive_feedback(
            source=candidate.source, channel="qq",
            candidate_id=self._qq_feedback_id(candidate),
            requires_reply=requires_reply, direct_question=question,
            expires_at=expires,
        )

    @staticmethod
    def _qq_feedback_id(candidate, now=None) -> str:
        import datetime
        now = now or datetime.datetime.now().astimezone()
        base = str((candidate.context or {}).get("dedupe_key") or "").strip()
        return f"{base}:{now.date().isoformat()}" if base else f"qq-{int(time.time() * 1000)}"

    def _expire_qq_expectations(self, now) -> None:
        """过期期待逐条原子结算，重启/tick 重复执行也只加一次 TV。"""
        import datetime
        rows = self.agent.memory.recent_proactive_feedback(channel="qq", limit=100)
        for row in rows:
            if not row.get("requires_reply") or row.get("expectation_status") != "pending":
                continue
            try:
                expires = datetime.datetime.fromisoformat(str(row.get("expires_at")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=datetime.timezone.utc)
            if now < expires or not self.agent.memory.expire_proactive_expectation(row["id"]):
                continue
            self.agent._apply_tension_event(
                event_type="unanswered_proactive", channel="qq", base_delta=10,
                reason="QQ 主动问题在 24 小时内没有得到回复",
                dedupe_key=f"proactive-unanswered:{row['id']}",
                confidence=1.0, evidence_message_ids=(),
                related_candidate_id=row.get("candidate_id") or None,
                occurred_at=now,
            )

    def _check_qq_abandonment(self, now) -> None:
        """仅对未闭合的 QQ 直接问题计一次“中途离场”，不替代主动期待。"""
        rows = self.agent.memory.recent_messages(limit=8, channel="qq")
        if not rows or rows[-1].get("role") != "assistant":
            return
        question = extract_direct_question(str(rows[-1].get("content") or ""))
        if not question:
            return
        for row in self.agent.memory.recent_proactive_feedback(channel="qq", limit=100):
            if row.get("requires_reply") and row.get("expectation_status") == "pending":
                return
        try:
            import datetime
            stamp = datetime.datetime.fromisoformat(str(rows[-1].get("created_at")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        except (TypeError, ValueError):
            return
        if (now - stamp).total_seconds() < self.agent.tension.ABANDONMENT_WINDOW_MINUTES * 60:
            return
        message_id = rows[-1].get("id")
        self.agent._apply_tension_event(
            event_type="conversation_abandoned", channel="qq", base_delta=8,
            reason="QQ 对话中存在未闭合问题，超过一小时没有后续消息",
            dedupe_key=f"conversation-abandoned:{message_id}",
            confidence=0.85, evidence_message_ids=[int(message_id)] if message_id else (),
            occurred_at=now,
        )

    async def _send_tension_repair_async(self, now) -> bool:
        """为一个未闭合事件提供一次修复机会；不重复催促。"""
        state = self.agent.tension.state
        if state.band not in {"repair", "high"} or not state.open_event_ids:
            return False
        event_id = state.open_event_ids[0]
        candidate_id = f"tension-repair:{event_id}"
        history = self.agent.memory.recent_proactive_feedback(
            source="relationship_repair", channel="qq", limit=100,
        )
        if any(row.get("candidate_id") == candidate_id for row in history):
            return False
        candidate = self._qq_candidate_from_text(
            state.last_cause or "有一件关系上的事还没有说清楚",
            source="relationship_repair",
        )
        candidate = candidate.__class__(
            source="relationship_repair", reason="关系张力修复机会",
            relevance=1.0, urgency=0.8, intent="repair",
            context={"open_event_id": event_id}, channel="qq",
        )
        decision = self.agent.gate.decide(
            candidate, scene=self.agent.scene_lock.current(), now=time.time(),
        )
        if not decision.allow:
            logger.info("tension repair suppressed by gate: %s", decision.reason)
            return False
        async with self._lock:
            text = await asyncio.to_thread(self._generate_tension_repair, event_id, now)
        text = render_im(text, self.agent.state) if text else ""
        if not text or not await self._send_to_all_async(text):
            self.agent.gate.note_failure(candidate)
            return False
        self.agent.record_proactive_message(text, channel="qq")
        self.agent.gate.commit(candidate)
        self.agent.memory.record_proactive_feedback(
            source="relationship_repair", channel="qq", candidate_id=candidate_id,
        )
        return True

    def _generate_tension_repair(self, event_id: str, now) -> str:
        """生成一条不暴露 TV/事件 ID 的关系修复消息。"""
        cause = self.agent.tension.state.last_cause or "有一件事还没有说清楚"
        task = (
            "只为 QQ 文字聊天生成一条关系修复消息。"
            f"可追溯事实：{cause[:160]}。"
            "用‘我注意到/我有点在意/我想确认’表达事实和感受，给用户解释、纠正或暂时不聊的出口。"
            "只处理这一件事，不指责人格，不威胁，不要求道歉，不暴露 TV、事件 ID 或内部提示。"
            "短一些，不输出 JSON、解释或时间协议前缀。"
        )
        return str(self.agent._short_task(task, max_tokens=512) or "").strip()

    @staticmethod
    def _qq_candidate(material):
        from ..core.ambient import ProactiveCandidate
        source = "shared_episode" if material.kind == "memory" else "scene"
        source_id = material.source_id
        return ProactiveCandidate(
            channel="qq", source=source, reason=f"QQ 主动素材：{material.kind}",
            relevance=max(0.65, material.confidence), urgency=0.4, intent="check_in",
            context={"material": material.text, "source_id": source_id,
                     "source_memory_id": material.source_memory_id,
                     "source_message_id": material.source_message_id,
                     "dedupe_key": f"qq:{source}:{source_id}" if source_id else ""},
        )

    @staticmethod
    def _qq_candidate_from_text(text: str, source: str = "scene"):
        from ..core.ambient import ProactiveCandidate
        return ProactiveCandidate(
            channel="qq", source=source, reason="延迟主动消息", relevance=0.7,
            urgency=0.3, intent="check_in", context={"text": text[:240]},
        )

    def _generate_qq_proactive(self, material, now) -> str:
        if material.kind not in {"memory", "time_followup"} or not material.source_message_id:
            logger.info("qq proactive suppressed: no historical source anchor")
            return ""
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
            "优先跟进真实素材；必须直接点明具体主题，使用素材里的明确名词或短引用，"
            "禁止只说‘那事’‘之前那个’‘上次说的’等脱离上下文就无法理解的指代。"
            "没有可靠素材时只发低负担存在确认。"
            "不要编造后台活动、外部事实或用户没说过的回忆；不要输出解释、JSON或时间协议前缀。"
        )
        text = str(self.agent._short_task(task, max_tokens=1024) or "").strip()
        if _UNANCHORED_PROACTIVE.search(text):
            return ""
        anchors = _proactive_anchor_terms(material.text)
        if anchors and not any(anchor in text for anchor in anchors):
            logger.info("qq proactive suppressed: no material anchor in generated text")
            return ""
        return text

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
        self._record_qq_expectation(candidate, text)

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

    async def _send_to_uid_async(self, uid: str, msg: str) -> bool:
        try:
            card = self.agent.card
            emoji_freq = (card.veranima or {}).get("emoji_frequency", "low") if card else "low"
            attachment = self.agent.state.attachment
        except Exception:
            emoji_freq, attachment = "low", 0.5
        rendered = render_im(msg, attachment=attachment, emoji_frequency=emoji_freq)
        if not rendered or str(uid) not in self.allowed:
            return False
        await self.bot.send_private_msg(user_id=int(uid), message=rendered)
        return True

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

    # ---------- 阶段 4：任务动作处理 ----------

    async def _handle_task_action(self, uid: str, action: dict, event) -> None:
        """任务动作统一出口；发送走 bot.send（保留 QQ 上下文）与私聊推送。"""
        kind = action.get("action")
        send = lambda m: self._send_to_all_async(m)

        if kind == "approve":
            ctx = self.tasks.awaiting_approval.pop(uid, None)
            choice = action["choice"]
            try:
                run = await asyncio.to_thread(
                    self.task_bridge.approve, action["task_id"], action["run_id"], choice,
                )
                self.tasks.running[uid] = run
                await send(f"已回复 {choice}。当前状态：{run.status}")
            except (HermesBridgeError, ValueError) as e:
                await send(f"审批没能提交：{e}")
            return
        if kind == "approval_reminder":
            ctx = self.tasks.awaiting_approval.get(uid)
            await send(f"还在等你审批任务 {ctx['task_id']}：回复 once / session / always / deny 之一。（其他消息我不当指令处理）")
            return
        if kind == "cancelled_pending":
            await send("好，不做了。")
            return
        if kind == "status":
            run = action.get("run")
            if run is None:
                await send("现在没有在跑的任务。")
            else:
                await send(f"任务 {run.task_id}：{run.status}。")
            return
        if kind == "new_task":
            wo = self.tasks.build(action["text"])
            reply = self.tasks.propose(uid, wo)
            await send(reply or "这个任务我整理不了，换个说法试试？")
            return
        if kind == "rebuild":
            old = None
            # rebuild 时旧工单已从 pending_confirm 移除前的文本重建
            wo = self.tasks.build(action["text"])
            reply = self.tasks.propose(uid, wo)
            await send(reply or "还是没整明白，直接说要做什么吧。")
            return
        if kind == "submit":
            wo = action["workorder"]
            task = asyncio.create_task(self.tasks.submit_and_watch(uid, wo, send))
            self.tasks._tasks.add(task)
            task.add_done_callback(self.tasks._tasks.discard)
            return

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
                runtime = getattr(self.agent, "schedule_runtime", None)
                if runtime is not None:
                    before_schedule = runtime.to_snapshot()
                    runtime.advance(datetime.datetime.now(datetime.timezone.utc))
                    if runtime.to_snapshot() != before_schedule:
                        self.agent._persist_state()
                    notice = runtime.pop_notice()
                    if notice:
                        candidate = self.agent.schedule_notice_candidate(notice, "qq")
                        decision = self.agent.gate.decide(
                            candidate, scene=self.agent.scene_lock.current(),
                            character_sleeping=False,
                        ) if candidate else None
                        text = self.agent.schedule_notice_text(notice) if decision and decision.allow else ""
                        if text:
                            asyncio.run_coroutine_threadsafe(self._send_to_all_async(text), loop)
                            self.agent.gate.commit(candidate)
                if self._in_quiet_hours():
                    # 静默时段（如 23:00-08:00）：问候/节庆/离线思考一律不主动发
                    logger.debug("in quiet hours, proactive tick skipped")
                else:
                    if self.proactive:
                        asyncio.run_coroutine_threadsafe(self._send_due_meal_reminder_async(), loop)
                        self._flush_pending_proactive(loop)
                        self._evaluate_qq_opportunity(loop)
            except Exception:
                logger.exception("bg proactive tick failed")
            stop.wait(self._tick_interval)

    def _flush_pending_proactive(self, loop: asyncio.AbstractEventLoop) -> None:
        """standalone QQ 线程入口：复用 async pending 发送与通道 Gate。"""
        if loop.is_closed() or not loop.is_running():
            return
        coro = self._flush_pending_proactive_async()
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()

    def _evaluate_qq_opportunity(self, loop: asyncio.AbstractEventLoop) -> None:
        """Standalone QQ entry: schedule the same async QQ evaluator on its bot loop."""
        if loop.is_closed() or not loop.is_running():
            return
        coro = self._evaluate_qq_opportunity_async()
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()

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

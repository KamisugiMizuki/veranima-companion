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
import logging
import random
import threading
import time

import httpx

from aiocqhttp import CQHttp, Event, Message

from ..core.agent import Agent
from ..core.render import render_im

logger = logging.getLogger(__name__)


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
        self.stickers = sticker_library  # 8.6.3 表情包库（None = 关闭）
        self._lock = asyncio.Lock()
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
        # 8.6.3 表情包入库：没见过的图 → LLM 标注 → 入库（后台线程，不阻塞回复）
        if images:
            await asyncio.to_thread(self._ingest_stickers, [raw for _, raw in images])
        # 串行处理：agent 内部状态（历史/记忆/学习）有顺序依赖，禁止并发写
        async with self._lock:
            # M3a 通道互斥：QQ 消息到达 → 记 QQ 通道活跃（桌宠感知降功耗）
            try:
                self.agent.activity.touch("qq")
            except Exception:
                pass
            result = await asyncio.to_thread(self.agent.handle, text, [d for d, _ in images], channel="im")
        self._last_user_activity = time.time()
        if result.reply:
            await self.bot.send(event, result.reply)
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

    def _ingest_stickers(self, raw_images: list[bytes]) -> None:
        """8.6.3 表情包入库：没见过的 → LLM 标注 → 存库。

        判重用 dHash（同图不同尺寸/压缩也识别）；标注失败不强行入库。
        """
        if self.stickers is None:
            return
        for raw in raw_images:
            try:
                if self.stickers.find_similar(raw):
                    continue  # 见过，不重复入库
                from ..core.agent import _data_url_from_bytes
                data_url = _data_url_from_bytes(raw)
                meta = self.agent.annotate_sticker(data_url)
                if meta:
                    self.stickers.add(raw, **meta)
            except Exception as e:
                logger.debug("sticker ingest failed: %s", e)

    def _pick_sticker_for_reply(self, reply: str) -> str | None:
        """8.6.3 表情包输出：按回复情绪宽松匹配，返回本地文件路径或 None。

        约束：情绪命中才发（宽松：相近即可）；低使用次数优先。
        """
        if self.stickers is None or len(self.stickers) == 0:
            return None
        mood = self._reply_mood(reply)
        if not mood:
            return None
        cands = self.stickers.find_for_mood(mood, limit=3)
        if not cands:
            return None
        entry = cands[0]
        self.stickers.record_use(entry)
        return str(self.stickers.root / entry.file)

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
        """提取消息纯文本（CQ 码剥离；图片段单独走 _collect_images）。"""
        msg = event.get("message", "")
        if isinstance(msg, Message):
            return msg.extract_plain_text().strip()
        return str(msg).strip()

    async def _collect_images(self, event: Event) -> list[tuple[str, bytes]]:
        """8.6.2 图像输入：提取消息中的图片段并下载。

        返回 [(data_url, raw_bytes), ...]；下载失败/无图片段返回 []。
        data_url 供 LLM 多模态看图；raw_bytes 供 8.6.3 表情包入库。
        """
        msg = event.get("message", "")
        if not isinstance(msg, Message):
            return []
        urls = []
        for seg in msg:
            if seg.get("type") != "image":
                continue
            data = seg.get("data") or {}
            url = data.get("url") or ""
            # 只认 http(s) url；file 只是本地文件名（如 ab.png），不是可下载地址
            if url.startswith(("http://", "https://")):
                urls.append(url)
        if not urls:
            return []
        results = await asyncio.gather(
            *(asyncio.to_thread(self._download_image, u) for u in urls)
        )
        ok = [r for r in results if r]
        if len(ok) < len(urls):
            logger.warning("image download failed: %d/%d images dropped", len(urls) - len(ok), len(urls))
        return [(data_url, raw) for data_url, raw in ok]

    @staticmethod
    def _download_image(url: str) -> tuple[str, bytes] | None:
        """下载图片为 (data_url, raw_bytes)。失败返回 None（降级纯文本）。"""
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url)
                resp.raise_for_status()
            ctype = resp.headers.get("content-type", "image/jpeg")
            if not ctype.startswith("image/"):
                return None
            import base64
            b64 = base64.b64encode(resp.content).decode()
            return f"data:{ctype};base64,{b64}", resp.content
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
                        for msg in self.agent.tick_proactive():
                            self._send_to_all(loop, msg)
                        self._flush_pending_proactive(loop)
                    if self.offline is not None:
                        self._tick_offline_think(loop)
            except Exception:
                logger.exception("bg proactive tick failed")
            stop.wait(self._tick_interval)

    def _flush_pending_proactive(self, loop: asyncio.AbstractEventLoop) -> None:
        """延迟主动消息：对话静默满 proactive_delay_minutes 后发送（2026-08-04）。

        用户刚发完消息时回复 + 主动双连发很突兀；等对话冷下来再发，
        像真人"过了会儿想起一件事"。
        """
        if not self._pending_proactive:
            return
        if self._last_user_activity is None:
            return
        if time.time() - self._last_user_activity < self.proactive_delay_minutes * 60:
            return  # 对话还没冷下来
        msg = self._pending_proactive
        self._pending_proactive = None
        logger.info("pending proactive flushed after %dmin: %s", self.proactive_delay_minutes, msg[:60])
        self._send_to_all(loop, msg)

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
        """8.7.4 离线思考 + M3a 心跳：静默窗口命中 → late_reply 优先 → heartbeat 兜底。"""
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

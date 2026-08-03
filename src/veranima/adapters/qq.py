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

from aiocqhttp import CQHttp, Event, Message

from ..core.agent import Agent

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
        rand: random.Random | None = None,
    ):
        self.silence_minutes = max(1, int(silence_minutes))
        self.probability = max(0.0, min(1.0, probability))
        self._rand = rand or random.Random()
        self._fired_at: float | None = None  # 上次触发时间（None = 未触发过）

    def due(self, now: float, last_activity: float | None) -> bool:
        """静默超过 N 分钟且本窗口未触发 → 掷骰决定是否触发。"""
        if last_activity is None:
            return False
        if now - last_activity < self.silence_minutes * 60:
            return False
        if self._fired_at is not None and now - self._fired_at < self.silence_minutes * 60:
            return False  # 本窗口已触发过，再静默满一个窗口才可能再触发
        if self._rand.random() >= self.probability:
            return False
        self._fired_at = now
        return True


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
    ):
        self.agent = agent
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.allowed = {str(q) for q in allowed_qq}
        self.proactive = proactive
        self.offline = offline_think or OfflineThinkTimer()
        self._tick_interval = tick_interval
        self._lock = asyncio.Lock()
        self._last_user_activity: float | None = None
        self.bot = CQHttp(access_token=access_token or None, message_class=Message)
        self._register()

    # ---------- 消息处理 ----------

    def _register(self) -> None:
        @self.bot.on_message("private")
        async def _on_private(event: Event) -> None:
            await self._handle_private(event)

    async def _handle_private(self, event: Event) -> None:
        """私聊消息：白名单过滤 → 文本提取 → 串行 handle → 回复（含主动消息）。"""
        uid = str(event.get("user_id", ""))
        if uid not in self.allowed:
            logger.info("ignored private message from non-whitelist qq=%s", uid)
            return
        text = self._plain_text(event)
        if not text:
            logger.info("no plain text in message from qq=%s, skipped", uid)
            return
        logger.info("qq=%s >> %s", uid, text[:80])
        # 串行处理：agent 内部状态（历史/记忆/学习）有顺序依赖，禁止并发写
        async with self._lock:
            result = await asyncio.to_thread(self.agent.handle, text)
        self._last_user_activity = time.time()
        if result.reply:
            await self.bot.send(event, result.reply)
            logger.info("qq=%s << %s", uid, result.reply[:80])
        if result.proactive_msg:
            await self.bot.send(event, result.proactive_msg)
            logger.info("qq=%s << (主动) %s", uid, result.proactive_msg[:80])

    @staticmethod
    def _plain_text(event: Event) -> str:
        """提取消息纯文本（CQ 码剥离；8.6 图像能力未实现，图片段被忽略）。"""
        msg = event.get("message", "")
        if isinstance(msg, Message):
            return msg.extract_plain_text().strip()
        return str(msg).strip()

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
                if self.proactive:
                    for msg in self.agent.tick_proactive():
                        self._send_to_all(loop, msg)
                if self.offline is not None:
                    self._tick_offline_think(loop)
            except Exception:
                logger.exception("bg proactive tick failed")
            stop.wait(self._tick_interval)

    def _tick_offline_think(self, loop: asyncio.AbstractEventLoop) -> None:
        """8.7.4 离线思考：静默窗口命中 + 模型已加载 → late_reply → 发送。"""
        if not self.offline.due(time.time(), self._last_user_activity):
            return
        model_check = getattr(self.agent.llm, "is_model_loaded", None)
        if model_check is not None and not model_check():
            logger.info("offline think skipped: model not loaded")
            return
        msg = self.agent.late_reply()
        if msg:
            logger.info("offline think reply: %s", msg[:60])
            self._send_to_all(loop, msg)

    def _send_to_all(self, loop: asyncio.AbstractEventLoop, msg: str) -> None:
        """后台线程 → bot 事件循环，向白名单 QQ 号发送私聊消息。"""
        for uid in self.allowed:
            fut = asyncio.run_coroutine_threadsafe(
                self.bot.send_private_msg(user_id=int(uid), message=msg), loop
            )
            try:
                fut.result(timeout=15)
                logger.info("proactive msg sent to qq=%s: %s", uid, msg[:60])
            except Exception:
                logger.exception("failed to send proactive msg to qq=%s", uid)

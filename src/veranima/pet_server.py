"""桌宠核心 WS 服务端（R3_SPEC 1 进程架构 A + 3 通信协议）。

最小实现（R3 PoC）：
- 监听 127.0.0.1:8765，接受桌宠壳连接（单客户端）
- 收：poke（戳一下）、drag（拖拽）、ping
- 发：speak / bubble / state / stop_speak

独立进程运行：python -m veranima.pet_server
后续接入 Agent：connect_agent(agent) 后 speak 消息走真实 Agent。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid

import websockets

from veranima.config import load_config, save_config
from veranima.llm.client import _split_sentences
from veranima.core.render import render_tts

logger = logging.getLogger("veranima.pet_server")

PORT = 8765
MAX_WS_MESSAGE_BYTES = 64 * 1024 * 1024


class PetServer:
    """桌宠 WS 服务：维护单个客户端连接，收发协议消息。"""

    def __init__(self, host: str = "127.0.0.1", port: int = PORT) -> None:
        self.host = host
        self.port = port
        self.session_id = uuid.uuid4().hex[:12]
        self._client: websockets.ServerConnection | None = None
        self._send_lock = asyncio.Lock()
        self._agent = None  # 后续接 Agent；PoC 阶段 None
        self._agent_lock = asyncio.Lock()  # agent.handle 串行化（SQLite 游标非线程安全，并发实测 "no more rows available"）
        self._qq_adapter = None
        self._qq_task: asyncio.Task | None = None
        # R2 状态与取消（R2_SPEC 5）：递增 turn_id；新输入打断时旧 turn 的
        # 迟到结果由壳端按 turn_id 丢弃（低成本递增，不引入任务编排库）
        self._turn_seq = 0
        self._current_turn = 0
        self._current_request_id = ""
        self._reply_task: asyncio.Task | None = None
        self._cancelled_turns: set[int] = set()
        self._turn_clients: dict[int, object] = {}
        self._tts = None    # TTSClient（可选；未配置则桌宠只显气泡不发声）
        self._bilingual = False  # 角色双语（character.json veranima.bilingual.enabled）
        self.attention_cfg = {}  # 视觉注意力配置（VISION_SPEC 4，config.yaml attention: 段）
        self._obs_cache: dict = {}  # VISION_SPEC 5：观察缓存（120s TTL，region_key → ts）
        self._obs_budget_day = ""
        self._obs_budget_count = 0   # VISION_SPEC 5：observe_daily_budget 日预算
        self._seen_attention_events: set[str] = set()
        self._presence_was_absent = False  # R4 在场转变检测（VISION_SPEC L0）

    def connect_agent(self, agent) -> None:
        """接入 Agent（正式版：poke/speak 走 agent.handle(channel='tts')）。"""
        self._agent = agent
        # 双语标志：ja_text 为空时防御（中文送日语模型会怪音，2026-08-19 实测）
        try:
            card = (agent.card or {})
            self._bilingual = bool(((card.veranima or {}).get("bilingual") or {}).get("enabled"))
        except Exception:
            self._bilingual = False

    def connect_tts(self, tts) -> None:
        """接入 TTS（远程/本地统一 OpenAI 兼容接口；未配置则跳过合成）。"""
        self._tts = tts

    def connect_qq(self, adapter) -> None:
        """接入 QQ 适配器；adapter 必须复用本实例的 Agent/agent_lock。"""
        self._qq_adapter = adapter

    async def tick_presence(self) -> bool:
        """R1 无缝衔接（R1_SPEC 4.召回）：L0 在场检测 → absent→present 转变 → 衔接语。

        返回是否发送了衔接语。无 agent / 用户仍在场 / 从未 absent 不触发。
        """
        from veranima.core.presence import presence
        if self._agent is None:
            return False
        now_present = presence()
        if now_present and self._presence_was_absent:
            self._presence_was_absent = False
            try:
                msg, ja = await asyncio.to_thread(self._agent.seamless_greeting)
                if msg:
                    await self.speak(msg, tts_text=ja or None)
                    return True
            except Exception as e:
                logger.warning("seamless greeting failed: %s", e)
                return False
        if not now_present:
            self._presence_was_absent = True
        return False

    # ---------- 对外发送 ----------
    async def _call_agent(self, text: str, images: list[str] | None = None):
        """agent.handle 串行化调用（SQLite 游标非线程安全；桌宠/QQ 并发实测冲突）。"""
        async with self._agent_lock:
            if images:
                return await asyncio.to_thread(self._agent.handle, text, images, channel="tts")
            return await asyncio.to_thread(self._agent.handle, text, channel="tts")

    def _next_turn(self) -> int:
        """R2 递增 turn_id（R2_SPEC 5：低成本递增，不引入任务编排库）。"""
        self._turn_seq += 1
        return self._turn_seq

    async def _observe_event(self, att, ev):
        """Run one privacy-checked L3 observation for a fixation event."""
        from veranima.core.attention.events import Observation
        try:
            if ev.kind != "fixation_shift" or time.time() > ev.expires_at:
                return Observation(event_id=ev.event_id, category="unknown", confidence=0.0)
            import datetime
            today = datetime.date.today().isoformat()
            if today != self._obs_budget_day:
                self._obs_budget_day = today
                self._obs_budget_count = 0
            budget = int((self.attention_cfg or {}).get("observe_daily_budget", 120))
            if self._obs_budget_count >= budget:
                logger.info("visual: event=%s action=blocked reason=daily_budget", ev.event_id)
                return Observation(event_id=ev.event_id, category="unknown", confidence=0.0)
            policy = getattr(att, "policy", None)
            if policy is not None:
                verdict = policy.policy_action(ev.app_name, ev.window_title, ev.window_category)
                if verdict["action"] != "capture":
                    logger.info("visual: event=%s action=blocked reason=%s",
                                ev.event_id, verdict["reason"])
                    return Observation(event_id=ev.event_id, category="unknown", confidence=0.0,
                                       sensitive_redacted=verdict["category"] == "sensitive")
            from veranima.core.attention.perception import grab_color_region
            from veranima.core.attention.observer import observe
            crop_ratio = float((self.attention_cfg or {}).get("crop_ratio", 0.30))
            crop = await asyncio.to_thread(grab_color_region, ev.region, crop_ratio)
            if not crop:
                return Observation(event_id=ev.event_id, category="unknown", confidence=0.0)
            obs = await asyncio.to_thread(
                observe, self._agent.llm, crop,
                window_title=ev.window_title,
                category_hint=ev.window_category,
            )
            if not obs.is_valid or obs.expired:
                return Observation(event_id=ev.event_id, category="unknown", confidence=0.0)
            self._obs_budget_count += 1
            return Observation(
                event_id=ev.event_id, summary=obs.summary, category=obs.category,
                notable=obs.notable, confidence=obs.confidence,
                sensitive_redacted=obs.sensitive_redacted, expires_at=obs.expires_at,
            )
        except Exception as e:
            logger.debug("visual observe failed: %s", e)
            return Observation(event_id=getattr(ev, "event_id", ""),
                               category="unknown", confidence=0.0)

    async def _process_attention_event(self, att, ev) -> bool:
        """Consume one event through privacy, observation, recall and R4 gate."""
        from veranima.core.ambient import ProactiveCandidate
        now = time.time()
        if not ev.event_id or ev.event_id in self._seen_attention_events:
            return False
        self._seen_attention_events.add(ev.event_id)
        if len(self._seen_attention_events) > 2048:
            self._seen_attention_events.pop()
        if now > ev.expires_at:
            logger.info("visual: event=%s action=drop reason=expired", ev.event_id)
            return False
        logger.info("visual: event=%s state=%s source=%s reason=%s confidence=%.2f",
                    ev.event_id, ev.kind, ev.source, ev.reason, ev.confidence)
        if self._agent is None or ev.kind != "fixation_shift":
            return False  # window_switch only updates metadata; never captures or speaks
        if (self.attention_cfg or {}).get("enabled", True) is False or (self.attention_cfg or {}).get("paused", False):
            return False
        activity = getattr(self._agent, "activity", None)
        if activity and activity.active("qq"):
            logger.info("visual: event=%s action=blocked reason=qq_active", ev.event_id)
            return False
        cache_ttl = float((self.attention_cfg or {}).get("observe_cache_ttl_sec", 120))
        region_key = ",".join(f"{v:.2f}" for v in ev.region)
        cache_key = f"{ev.window_category}:{region_key}"
        if now - self._obs_cache.get(cache_key, 0.0) < cache_ttl:
            logger.debug("visual: event=%s action=blocked reason=observe_cache", ev.event_id)
            return False
        obs = await self._observe_event(att, ev)
        if not obs.is_valid or obs.expired:
            return False
        self._obs_cache[cache_key] = now
        query = f"{obs.category} {obs.summary}".strip()
        async with self._agent_lock:
            hits = await asyncio.to_thread(self._agent.memory.recall, query, top_k=5)
        related = next((entry for entry in hits
                        if entry.layer in ("episodic", "procedural") and
                        entry.confidence >= 0.65), None)
        logger.info("visual: event=%s action=observed category=%s matched=%s",
                    ev.event_id, obs.category, bool(related))
        if related is None:
            return False
        gate = getattr(self._agent, "gate", None)
        scene_lock = getattr(self._agent, "scene_lock", None)
        if gate is None or scene_lock is None:
            return False
        cand = ProactiveCandidate(
            source="attention", reason=f"视觉观察关联共同经历：{obs.summary[:40]}",
            relevance=max(0.65, min(1.0, obs.confidence)), urgency=0.4,
            intent="bridge",
            context={"event_id": ev.event_id, "category": obs.category,
                     "summary": obs.summary, "memory_id": related.id},
        )
        decision = gate.decide(
            cand, scene=scene_lock.current(),
            other_channel_active=activity.blocking("pet") if activity else False,
        )
        if not decision.allow:
            logger.info("visual: event=%s action=suppressed reason=%s",
                        ev.event_id, decision.reason)
            return False
        async with self._agent_lock:
            proactive, ja = await asyncio.to_thread(
                self._agent.proactive_from_visual, obs.category,
                related.content,
            )
        if not proactive:
            return False
        self._agent.gate.commit(cand)
        try:
            self._agent.memory.record_proactive_feedback(source="attention")
        except Exception as e:
            logger.debug("feedback record failed: %s", e)
        logger.info("visual: event=%s action=proactive", ev.event_id)
        await self.speak(proactive, tts_text=ja or None)
        return True

    async def speak(self, text: str, tags: list | None = None, tts_text: str | None = None,
                    *, turn_id: int | None = None, request_id: str | None = None) -> bool:
        """推送一条完整回复；所有事件共享 turn/request 上下文。"""
        import base64

        tags = tags or []
        portrait = tags[0] if tags else ""
        text_zh = tts_text and text or ""
        speak_text = (tts_text or text).strip()
        if turn_id is not None and turn_id in self._cancelled_turns:
            return False
        if not await self.reply_start(turn_id=turn_id, request_id=request_id):
            return False
        if self._tts is None or not speak_text or (self._bilingual and not tts_text):
            await self.reply_segment(text=text, portrait=portrait, text_zh=text_zh,
                                     turn_id=turn_id, request_id=request_id)
            return await self.reply_end(turn_id=turn_id, request_id=request_id)
        try:
            audio = await asyncio.to_thread(self._tts.synthesize, speak_text)
            if turn_id is not None and turn_id in self._cancelled_turns:
                return False
            await self.reply_segment(
                text=text,
                audio_b64=base64.b64encode(audio).decode() if audio else "",
                portrait=portrait,
                text_zh=text_zh,
                turn_id=turn_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("tts synthesize failed (bubble only): %s", e)
            await self.reply_error(code="tts_failed", recoverable=True,
                                   turn_id=turn_id, request_id=request_id)
            await self.reply_segment(text=text, portrait=portrait, text_zh=text_zh,
                                     turn_id=turn_id, request_id=request_id)
        return await self.reply_end(turn_id=turn_id, request_id=request_id)

    async def speak_reply(self, reply, *, turn_id: int | None = None,
                          request_id: str | None = None) -> bool:
        """消费完整 Reply 的全部 TTS segments，不丢上下文。"""
        segments = render_tts(reply)
        if not segments:
            return await self.speak(getattr(reply, "text", ""), turn_id=turn_id,
                                    request_id=request_id)
        if not await self.reply_start(turn_id=turn_id, request_id=request_id):
            return False
        import base64
        for seg in segments:
            if turn_id is not None and turn_id in self._cancelled_turns:
                return False
            speak_text = (seg.text or "").strip()
            if not speak_text or getattr(seg, "suppress_tts", False):
                await self.reply_segment(text=seg.display_text or speak_text,
                                         tone=seg.tone, portrait=seg.portrait,
                                         turn_id=turn_id, request_id=request_id)
                continue
            text_zh = seg.display_text if seg.display_text and seg.display_text != speak_text else ""
            try:
                audio = None if self._tts is None else await asyncio.to_thread(
                    self._tts.synthesize, speak_text)
                await self.reply_segment(
                    text=text_zh or speak_text,
                    audio_b64=base64.b64encode(audio).decode() if audio else "",
                    tone=seg.tone, portrait=seg.portrait, text_zh=text_zh,
                    turn_id=turn_id, request_id=request_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("tts segment failed (bubble only): %s", e)
                await self.reply_segment(
                    text=text_zh or speak_text,
                    tone=seg.tone, portrait=seg.portrait, text_zh=text_zh,
                    turn_id=turn_id, request_id=request_id,
                )
        return await self.reply_end(turn_id=turn_id, request_id=request_id)

    async def speak_result(self, result, *, turn_id: int | None = None,
                           request_id: str | None = None) -> bool:
        """Reply 对象存在时走完整分段；旧 FakeAgent/兼容调用退回 speak。"""
        reply_obj = getattr(result, "reply_obj", None)
        if reply_obj is not None:
            return await self.speak_reply(reply_obj, turn_id=turn_id, request_id=request_id)
        return await self.speak(
            getattr(result, "reply", ""),
            tags=[result.portrait] if getattr(result, "portrait", "") else None,
            tts_text=getattr(result, "ja_text", "") or None,
            turn_id=turn_id,
            request_id=request_id,
        )

    async def bubble(self, text: str) -> bool:
        return await self._send({"type": "bubble", "text": text})

    async def push_state(self, **extra) -> bool:
        """R3 状态事件（R3_SPEC 1/2）：统一 payload 结构。"""
        return await self._send({
            "type": "state",
            "payload": {
                "status": extra.pop("status", "online"),
                "character": extra.pop("character", ""),
                "turn_id": extra.pop("turn_id", ""),
                **extra,
            },
        })

    def _reply_context(self, turn_id=None, request_id=None) -> tuple[int, str]:
        return (
            self._current_turn if turn_id is None else int(turn_id),
            self._current_request_id if request_id is None else str(request_id),
        )

    def _reply_deliverable(self, turn_id: int) -> bool:
        """Drop cancelled turns and replies owned by a replaced shell connection."""
        if turn_id in self._cancelled_turns:
            return False
        owner = self._turn_clients.get(turn_id)
        return owner is None or owner is self._client

    async def stop_speak(self) -> bool:
        """R3：打断当前回复 → reply_cancelled（R3_SPEC 1）。"""
        # asyncio.to_thread 无法真正杀掉底层线程；标记 turn 取消并让线程自然收尾，
        # 避免释放 _agent_lock 后下一轮与旧线程并发操作 SQLite。
        if self._current_turn:
            self._cancelled_turns.add(self._current_turn)
            if len(self._cancelled_turns) > 128:
                self._cancelled_turns = set(sorted(self._cancelled_turns)[-64:])
        return await self._send({
            "type": "reply_cancelled",
            "payload": {"turn_id": self._current_turn, "request_id": self._current_request_id},
        })

    async def reply_start(self, channel: str = "tts", *, turn_id=None, request_id=None) -> bool:
        """R3 协议：回复开始（R3_SPEC 1）。"""
        turn_id, request_id = self._reply_context(turn_id, request_id)
        if not self._reply_deliverable(turn_id):
            return False
        return await self._send({
            "type": "reply_start",
            "payload": {"turn_id": turn_id, "request_id": request_id, "channel": channel},
        })

    async def reply_segment(self, *, text: str, audio_b64: str = "",
                            tone: str = "", portrait: str = "", text_zh: str = "",
                            turn_id=None, request_id=None) -> bool:
        """R3 协议：回复段落（R3_SPEC 1）。"""
        turn_id, request_id = self._reply_context(turn_id, request_id)
        if not self._reply_deliverable(turn_id):
            return False
        return await self._send({
            "type": "reply_segment",
            "payload": {
                "turn_id": turn_id, "request_id": request_id,
                "text": text, "audio_b64": audio_b64,
                "tone": tone, "portrait": portrait, "text_zh": text_zh,
            },
        })

    async def reply_end(self, *, turn_id=None, request_id=None) -> bool:
        """R3 协议：回复结束（R3_SPEC 1）。"""
        turn_id, request_id = self._reply_context(turn_id, request_id)
        if not self._reply_deliverable(turn_id):
            return False
        return await self._send({
            "type": "reply_end",
            "payload": {"turn_id": turn_id, "request_id": request_id},
        })

    async def reply_error(self, code: str = "tts_failed", recoverable: bool = True,
                          *, turn_id=None, request_id=None) -> bool:
        """R3 协议：回复失败（R3_SPEC 1）。"""
        turn_id, request_id = self._reply_context(turn_id, request_id)
        if not self._reply_deliverable(turn_id):
            return False
        return await self._send({
            "type": "reply_error",
            "payload": {"turn_id": turn_id, "request_id": request_id,
                        "code": code, "recoverable": recoverable},
        })

    async def _send(self, msg: dict, *, client=None) -> bool:
        # R3 协议统一信封：event_id + ts（R3_SPEC 1）
        if "event_id" not in msg:
            msg["event_id"] = uuid.uuid4().hex[:8]
        if "ts" not in msg:
            msg["ts"] = int(time.time())
        msg.setdefault("session_id", self.session_id)
        if self._client is None:
            return False
        try:
            payload = msg.get("payload") or {}
            turn_id = payload.get("turn_id")
            try:
                owner = self._turn_clients.get(int(turn_id)) if turn_id not in (None, "") else None
            except (TypeError, ValueError):
                owner = None
            async with self._send_lock:
                if self._client is None or (client is not None and self._client is not client):
                    return False
                if owner is not None and self._client is not owner:
                    return False
                await self._client.send(json.dumps(msg, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("send failed: %s", e)
            if client is None or self._client is client:
                self._client = None
            return False

    # ---------- 接收处理 ----------
    async def _run_poke(self, turn_id: int, request_id: str, ws) -> None:
        try:
            if self._agent is not None:
                try:
                    r = await self._call_agent("（用户戳了戳桌宠）")
                    if self._client is ws and self._reply_deliverable(turn_id):
                        await self.speak_result(r, turn_id=turn_id, request_id=request_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("poke agent failed: %s", e)
                    if self._client is ws and self._reply_deliverable(turn_id):
                        await self.speak("嗯？叫我干嘛～", turn_id=turn_id, request_id=request_id)
            else:
                if self._client is ws and self._reply_deliverable(turn_id):
                    await self.speak("嗯？叫我干嘛～", turn_id=turn_id, request_id=request_id)
        except asyncio.CancelledError:
            raise

    async def _run_stream_talk(self, msg_text: str, turn_id: int, request_id: str, ws,
                               images: list[str] | None = None) -> None:
        try:
            if self._agent is None:
                if self._client is ws and self._reply_deliverable(turn_id):
                    await self.speak("（流式需要接入 agent）", turn_id=turn_id, request_id=request_id)
                return
            try:
                fb = self._agent.memory.recent_proactive_feedback(limit=3)
                pending = [f for f in fb if not f["responded"]]
                if pending:
                    self._agent.memory.record_proactive_feedback(
                        source=pending[-1]["source"], responded=True)
                    self._agent.gate.note_responded(pending[-1]["source"])
                if len(pending) >= 2:
                    self._agent.gate.note_ignored(pending[-1]["source"])
            except Exception as e:
                logger.debug("proactive feedback update failed: %s", e)
            r = await self._call_agent(msg_text, images)
            if self._client is ws and self._reply_deliverable(turn_id):
                await self.speak_result(r, turn_id=turn_id, request_id=request_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("stream_talk failed: %s", e)
            if self._client is ws and self._reply_deliverable(turn_id):
                await self.reply_error(code="reply_failed", recoverable=True,
                                       turn_id=turn_id, request_id=request_id)
                await self.reply_segment(text="（这条回复没有完成，再说一次？）",
                                         turn_id=turn_id, request_id=request_id)
                await self.reply_end(turn_id=turn_id, request_id=request_id)

    def _task_done(self, task: asyncio.Task) -> None:
        if self._reply_task is task:
            self._reply_task = None
        if not task.cancelled() and task.exception() is not None:
            logger.warning("reply task ended with error", exc_info=task.exception())

    async def _handle(self, ws: websockets.ServerConnection) -> None:
        old_client = self._client
        if old_client is not None and old_client is not ws:
            if self._current_turn:
                self._cancelled_turns.add(self._current_turn)
            try:
                await old_client.close(code=4001, reason="replaced by new shell")
            except Exception:
                pass
        self._client = ws
        logger.info("pet shell connected")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype in ("stop_speak", "cancel_reply"):
                    await self.stop_speak()
                    continue
                if mtype in ("poke", "stream_talk"):
                    # 新输入只打断旧任务；接收循环继续运行，stop/ping/config 不再排队。
                    await self.stop_speak()
                    self._current_turn = self._next_turn()
                    self._current_request_id = str(msg.get("request_id") or uuid.uuid4().hex)
                    turn_id = self._current_turn
                    request_id = self._current_request_id
                    self._turn_clients[turn_id] = ws
                    if len(self._turn_clients) > 128:
                        for old_turn in sorted(self._turn_clients)[:-64]:
                            self._turn_clients.pop(old_turn, None)
                    if mtype == "poke":
                        logger.info("poke received turn=%s request=%s", turn_id, request_id)
                        task = asyncio.create_task(self._run_poke(turn_id, request_id, ws))
                    else:
                        msg_text = str(msg.get("text") or "").strip()[:8000]
                        images = [str(x) for x in (msg.get("images") or []) if isinstance(x, str)][:4]
                        if not msg_text and not images:
                            continue
                        task = asyncio.create_task(
                            self._run_stream_talk(msg_text, turn_id, request_id, ws, images=images))
                    self._reply_task = task
                    task.add_done_callback(self._task_done)
                elif mtype == "drag":
                    pass  # 拖拽由壳自己处理，核心无需响应（PoC）
                elif mtype == "ping":
                    await self._send({"type": "pong"})
                elif mtype == "get_config":
                    # 设置窗口读配置：返回可编辑字段（api_key 打码）
                    cfg = load_config()
                    llm = cfg.get("llm", {})
                    key = llm.get("api_key", "")
                    masked = (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("****" if key else "")
                    qq = cfg.get("qq", {})
                    memory = cfg.get("memory", {}) or {}
                    attention = cfg.get("attention", {}) or {}
                    proactive = cfg.get("proactive", {}) or {}
                    await self._send({"type": "config", "id": msg.get("id"), "data": {
                        "llm": {"base_url": llm.get("base_url", ""), "model": llm.get("model", ""),
                                "temperature": llm.get("temperature", 0.8), "max_tokens": llm.get("max_tokens", 4096),
                                "timeout": llm.get("timeout", 120), "api_key": masked},
                        "tts": {"base_url": cfg.get("tts", {}).get("base_url", ""),
                                "model": cfg.get("tts", {}).get("model", ""),
                                "voice": cfg.get("tts", {}).get("voice", "")},
                        "stt": {"enabled": cfg.get("stt", {}).get("enabled", True),
                                "base_url": cfg.get("stt", {}).get("base_url", ""),
                                "model": cfg.get("stt", {}).get("model", ""),
                                "language": cfg.get("stt", {}).get("language", ""),
                                "input_device_id": cfg.get("stt", {}).get("input_device_id", "")},
                        "qq": {"allowed": qq.get("allowed", qq.get("allowed_qq", [])),
                               "proactive": qq.get("proactive", False),
                               "offline_think": {"enabled": (qq.get("offline_think") or {}).get("enabled", False)}},
                        "character_card": cfg.get("character_card", ""),
                        "pet": {"avatar_height": (cfg.get("pet") or {}).get("avatar_height", 200)},
                        "memory": {k: memory.get(k) for k in (
                            "embedding_model", "recall_top_k", "recall_threshold", "max_injected_chars",
                            "core_profile_budget", "section_budget", "session_budget", "decay_enabled",
                            "decay_interval_minutes", "curator_turns")},
                        "attention": {k: attention.get(k) for k in (
                            "enabled", "paused", "global_scan_sec", "mouse_focus_stay_sec",
                            "habituation_sec", "observe_cache_ttl_sec", "observe_daily_budget", "crop_ratio")},
                        "proactive": {k: proactive.get(k) for k in (
                            "enabled", "quiet_hours_enabled", "max_per_day", "min_gap_minutes", "source_gap_minutes")},
                    }})
                elif mtype == "search_history":
                    data = msg.get("data") or {}
                    query = str(data.get("query") or msg.get("query") or "")
                    before_id = data.get("before_id")
                    rows = self._agent.memory.search_messages(query, before_id=before_id) if self._agent else []
                    await self._send({"type": "history_search_results", "id": msg.get("id"), "query": query, "data": rows})
                elif mtype == "get_self_model":
                    chapters = self._agent.memory.list_self_model_chapters() if self._agent else []
                    await self._send({"type": "self_model", "id": msg.get("id"), "data": {"chapters": chapters}})
                elif mtype == "get_self_model_chapter":
                    chapter_id = (msg.get("data") or {}).get("id")
                    chapter = self._agent.memory.get_self_model_chapter(chapter_id) if self._agent and chapter_id else None
                    await self._send({"type": "self_model_chapter", "id": msg.get("id"), "data": chapter})
                elif mtype == "save_config":
                    # 设置窗口保存：白名单字段更新（全字段——之前只处理 llm/qq.allowed，
                    # character_card/tts/stt 等被静默丢弃，设置页形同虚设）
                    cfg = load_config()
                    d = msg.get("data", {})
                    llm = d.get("llm", {})
                    for k in ("base_url", "model", "temperature", "max_tokens", "timeout"):
                        if k in llm:
                            cfg.setdefault("llm", {})[k] = llm[k]
                    if llm.get("api_key") and "****" not in str(llm["api_key"]):
                        cfg.setdefault("llm", {})["api_key"] = str(llm["api_key"]).strip()
                    tts = d.get("tts", {})
                    for k in ("base_url", "model", "voice"):
                        if k in tts:
                            cfg.setdefault("tts", {})[k] = tts[k]
                    stt = d.get("stt", {})
                    for k in ("enabled", "base_url", "model", "language", "input_device_id"):
                        if k in stt:
                            cfg.setdefault("stt", {})[k] = stt[k]
                    qq = d.get("qq", {})
                    if "allowed" in qq:
                        cfg.setdefault("qq", {})["allowed_qq"] = qq["allowed"]
                    if "proactive" in qq:
                        cfg.setdefault("qq", {})["proactive"] = qq["proactive"]
                    if "offline_think" in qq:
                        cfg.setdefault("qq", {})["offline_think"] = qq["offline_think"]
                    if "character_card" in d and d["character_card"]:
                        cfg["character_card"] = d["character_card"]
                    pet = d.get("pet", {})
                    if "avatar_height" in pet:
                        cfg.setdefault("pet", {})["avatar_height"] = pet["avatar_height"]
                    # GUI-4：隐私与主动性（部分更新，不覆盖整段）
                    att = d.get("attention", {})
                    for k in ("enabled", "paused", "global_scan_sec", "observe_daily_budget"):
                        if k in att:
                            cfg.setdefault("attention", {})[k] = att[k]
                    pro = d.get("proactive", {})
                    for k in ("enabled", "quiet_hours_enabled", "max_per_day", "min_gap_minutes", "source_gap_minutes"):
                        if k in pro:
                            cfg.setdefault("proactive", {})[k] = pro[k]
                    memory = d.get("memory", {})
                    for k in ("embedding_model", "recall_top_k", "recall_threshold", "max_injected_chars",
                              "core_profile_budget", "section_budget", "session_budget", "decay_enabled",
                              "decay_interval_minutes", "curator_turns"):
                        if k in memory: cfg.setdefault("memory", {})[k] = memory[k]
                    attention = d.get("attention", {})
                    for k in ("enabled", "paused", "global_scan_sec", "mouse_focus_stay_sec",
                              "habituation_sec", "observe_cache_ttl_sec", "observe_daily_budget", "crop_ratio"):
                        if k in attention: cfg.setdefault("attention", {})[k] = attention[k]
                    save_config(cfg)
                    await self._send({"type": "config_saved", "id": msg.get("id"), "ok": True,
                                     "restart": "重启核心生效"})
                else:
                    logger.warning("unknown msg: %s", mtype)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            logger.info("pet shell disconnected")
            if self._client is ws:
                self._client = None

    async def run(self) -> None:
        logger.info("pet server on ws://%s:%d", self.host, self.port)
        async with websockets.serve(
            self._handle, self.host, self.port, max_size=MAX_WS_MESSAGE_BYTES, origins=[None],
        ):
            async def _warm_embedding():
                if self._agent is None:
                    return
                try:
                    await asyncio.to_thread(self._agent.memory.warm_embedding)
                    logger.info("embedding warmup complete")
                except Exception as e:
                    logger.warning("embedding warmup failed; first recall will retry: %s", e)
            asyncio.create_task(_warm_embedding())
            # R1 无缝衔接：30s 一次 L0 在场检测（absent→present → 衔接语）
            async def _presence_loop():
                while True:
                    try:
                        await self.tick_presence()
                    except Exception as e:
                        logger.warning("presence tick failed: %s", e)
                    await asyncio.sleep(30)
            asyncio.create_task(_presence_loop())

            # 视觉注意力（VISION_SPEC V1：AttentionScheduler 扫视-注视状态机）
            # 替代 R4_SPEC 1.x 的 VisualAttention（vision.py 过渡实现）
            async def _visual_loop():
                from veranima.core.attention import AttentionScheduler
                att = AttentionScheduler(llm=getattr(self._agent, "llm", None),
                                         config=self.attention_cfg)
                await asyncio.sleep(10)
                logger.info("visual: 注意力循环启动（VISION_SPEC V1）")
                while True:
                    try:
                        for ev in await asyncio.to_thread(att.tick):
                            try:
                                await self._process_attention_event(att, ev)
                            except Exception as e:
                                logger.warning("visual event consumer failed: %s", e)
                    except Exception as e:
                        logger.warning("visual tick failed: %s", e)
                    await asyncio.sleep(0.5)
            asyncio.create_task(_visual_loop())
            if self._qq_adapter is not None:
                async def _qq_loop():
                    try:
                        await self._qq_adapter.run_task()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("QQ adapter stopped; desktop core remains online")
                self._qq_task = asyncio.create_task(_qq_loop())
            await asyncio.Future()  # 常驻


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Veranima 桌宠核心 WS 服务")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    srv = PetServer(port=args.port)
    # Agent（与 QQ 端同一工厂：人格/记忆/打断全链路；未接则 stream_talk 无法工作）
    from veranima.app import create_agent
    from veranima.config import load_config
    cfg = load_config()
    agent = create_agent(cfg)
    srv.connect_agent(agent)
    logger.info("agent connected (character_card=%s)", cfg.get("character_card", ""))
    if (cfg.get("qq") or {}).get("enabled", False):
        try:
            from veranima.qq import build_adapter
            qq_adapter = build_adapter(cfg, agent, agent_lock=srv._agent_lock)
            srv.connect_qq(qq_adapter)
            logger.info("QQ adapter attached to shared desktop Agent")
        except Exception as e:
            logger.warning("QQ adapter disabled: %s", e)
    # 视觉注意力配置（VISION_SPEC 4：config.yaml attention: 段 → AttentionScheduler）
    srv.attention_cfg = cfg.get("attention", {}) or {}
    # TTS（远程/本地统一 OpenAI 兼容接口；未配置 base_url 则桌宠只显气泡）
    from veranima.tts.client import TTSClient
    tts_cfg = cfg.get("tts", {})
    if tts_cfg.get("base_url"):
        srv.connect_tts(TTSClient(tts_cfg))
        logger.info("TTS enabled: %s", tts_cfg["base_url"])
    else:
        logger.info("TTS disabled (base_url 未配置)")
    asyncio.run(srv.run())


if __name__ == "__main__":
    main()

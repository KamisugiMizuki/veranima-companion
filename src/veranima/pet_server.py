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


class PetServer:
    """桌宠 WS 服务：维护单个客户端连接，收发协议消息。"""

    def __init__(self, host: str = "127.0.0.1", port: int = PORT) -> None:
        self.host = host
        self.port = port
        self._client: websockets.ServerConnection | None = None
        self._agent = None  # 后续接 Agent；PoC 阶段 None
        self._agent_lock = asyncio.Lock()  # agent.handle 串行化（SQLite 游标非线程安全，并发实测 "no more rows available"）
        # R2 状态与取消（R2_SPEC 5）：递增 turn_id；新输入打断时旧 turn 的
        # 迟到结果由壳端按 turn_id 丢弃（低成本递增，不引入任务编排库）
        self._turn_seq = 0
        self._current_turn = 0
        self._tts = None    # TTSClient（可选；未配置则桌宠只显气泡不发声）
        self._bilingual = False  # 角色双语（character.json veranima.bilingual.enabled）
        self.attention_cfg = {}  # 视觉注意力配置（VISION_SPEC 4，config.yaml attention: 段）
        self._obs_cache: dict = {}  # VISION_SPEC 5：观察缓存（120s TTL，region_key → ts）
        self._obs_budget_day = ""
        self._obs_budget_count = 0   # VISION_SPEC 5：observe_daily_budget 日预算
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
    async def _call_agent(self, text: str):
        """agent.handle 串行化调用（SQLite 游标非线程安全；桌宠/QQ 并发实测冲突）。"""
        async with self._agent_lock:
            return await asyncio.to_thread(self._agent.handle, text, channel="tts")

    def _next_turn(self) -> int:
        """R2 递增 turn_id（R2_SPEC 5：低成本递增，不引入任务编排库）。"""
        self._turn_seq += 1
        return self._turn_seq

    async def _observe_event(self, att, ev) -> tuple[str, str]:
        """VISION_SPEC 5/6：事件 → L3 观察（区域裁剪 → LLM Observation）。

        返回 (summary, category)；失败/敏感返回 ("", "")。不写记忆、不 speak。
        """
        try:
            # VISION_SPEC 5：日预算（observe_daily_budget 默认 120，超出只保留 L0/L1）
            import datetime
            today = datetime.date.today().isoformat()
            if today != self._obs_budget_day:
                self._obs_budget_day = today
                self._obs_budget_count = 0
            budget = int((self.attention_cfg or {}).get("observe_daily_budget", 120))
            if self._obs_budget_count >= budget:
                logger.debug("visual: 日观察预算已用尽 (%d)", budget)
                return "", ""
            # 敏感窗口策略：命中不发图（VISION_SPEC 4）
            policy = getattr(att, "policy", None)
            if policy is not None:
                verdict = policy.policy_action(ev.note or "", ev.note or "")
                if verdict["action"] != "capture":
                    logger.debug("visual: %s", verdict["reason"])
                    return "", ""
            from veranima.core.attention.perception import grab_gray_downsampled, grab_region
            from veranima.core.attention.observer import observe
            frame = await asyncio.to_thread(grab_gray_downsampled)
            if frame is None:
                return "", ""
            # 归一化区域 → 像素（region 是 (x0,y0,x1,y1) 0-1）
            h, w = frame.shape[:2]
            x0 = int(ev.region[0] * w); y0 = int(ev.region[1] * h)
            x1 = max(x0 + 1, int(ev.region[2] * w)); y1 = max(y0 + 1, int(ev.region[3] * h))
            # VISION_SPEC 5：区域最大为屏幕短边 30%（crop_ratio）
            crop_ratio = float((self.attention_cfg or {}).get("crop_ratio", 0.30))
            short_side = min(h, w) * crop_ratio
            if (x1 - x0) > short_side:
                x1 = min(w, x0 + int(short_side))
            if (y1 - y0) > short_side:
                y1 = min(h, y0 + int(short_side))
            crop = await asyncio.to_thread(grab_region, frame, x0, y0, x1, y1)
            if not crop:
                return "", ""
            obs = await asyncio.to_thread(
                observe, self._agent.llm, crop, window_title=ev.note or "")
            if not obs.is_valid:
                return "", ""
            self._obs_budget_count += 1
            return obs.summary, obs.category
        except Exception as e:
            logger.debug("visual observe failed: %s", e)
            return "", ""

    async def speak(self, text: str, tags: list | None = None, tts_text: str | None = None) -> bool:
        """推送回复（R3 协议：reply_start → reply_segment → reply_end）。

        tts_text 指定时用它合成音频（R2 双语：ja 配音 / zh 显示）。
        整段方案：一次 POST /tts 合成整段 → 一条 reply_segment（音频+文本）。
        代价：首句延迟 = 整段合成时间（~0.57x 实时率）；换来分句链路的
        bug 全灭（重复推送/气泡…/队列去重——实测逐句方案引发多种问题）。
        TTS 失败保留文字（R3_SPEC 5：不清空文字），并发 reply_error 可恢复。
        """
        import base64

        tags = tags or []
        portrait = tags[0] if tags else ""
        text_zh = tts_text and text or ""
        speak_text = (tts_text or text).strip()

        await self.reply_start()
        if self._tts is None or not speak_text or (self._bilingual and not tts_text):
            # 无 TTS / 双语缺日语台词：纯气泡（不发音频，不送日语模型）
            await self.reply_segment(text=text, portrait=portrait, text_zh=text_zh)
            return await self.reply_end()

        try:
            audio = await asyncio.to_thread(self._tts.synthesize, speak_text)
            await self.reply_segment(
                text=text,
                audio_b64=base64.b64encode(audio).decode() if audio else "",
                portrait=portrait,
                text_zh=text_zh,
            )
        except Exception as e:
            logger.warning("tts synthesize failed (bubble only): %s", e)
            # R3：文字保留 + 可恢复错误
            await self.reply_error(code="tts_failed", recoverable=True)
            await self.reply_segment(text=text, portrait=portrait, text_zh=text_zh)
        return await self.reply_end()

    async def speak_reply(self, reply) -> bool:
        """R2/R3：消费完整 Reply 的全部 TTS segments，不丢 tone/portrait/zh。"""
        segments = render_tts(reply)
        if not segments:
            return await self.speak(getattr(reply, "text", ""))
        await self.reply_start()
        import base64
        for seg in segments:
            speak_text = (seg.text or "").strip()
            if not speak_text or getattr(seg, "suppress_tts", False):
                await self.reply_segment(text=seg.display_text or speak_text,
                                         tone=seg.tone, portrait=seg.portrait)
                continue
            text_zh = seg.display_text if seg.display_text and seg.display_text != speak_text else ""
            try:
                audio = None if self._tts is None else await asyncio.to_thread(self._tts.synthesize, speak_text)
                await self.reply_segment(text=text_zh or speak_text,
                                         audio_b64=base64.b64encode(audio).decode() if audio else "",
                                         tone=seg.tone, portrait=seg.portrait, text_zh=text_zh)
            except Exception as e:
                logger.warning("tts segment failed (bubble only): %s", e)
                await self.reply_segment(text=text_zh or speak_text,
                                         tone=seg.tone, portrait=seg.portrait, text_zh=text_zh)
        return await self.reply_end()

    async def speak_result(self, result) -> bool:
        """Reply 对象存在时走完整分段；旧 FakeAgent/兼容调用退回 speak。"""
        reply_obj = getattr(result, "reply_obj", None)
        if reply_obj is not None:
            return await self.speak_reply(reply_obj)
        return await self.speak(
            getattr(result, "reply", ""),
            tags=[result.portrait] if getattr(result, "portrait", "") else None,
            tts_text=getattr(result, "ja_text", "") or None,
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

    async def stop_speak(self) -> bool:
        """R3：打断当前回复 → reply_cancelled（R3_SPEC 1）。"""
        return await self._send({
            "type": "reply_cancelled",
            "payload": {"turn_id": self._current_turn},
        })

    async def reply_start(self, channel: str = "tts") -> bool:
        """R3 协议：回复开始（R3_SPEC 1）。"""
        return await self._send({
            "type": "reply_start",
            "payload": {"turn_id": self._current_turn, "channel": channel},
        })

    async def reply_segment(self, *, text: str, audio_b64: str = "",
                            tone: str = "", portrait: str = "", text_zh: str = "") -> bool:
        """R3 协议：回复段落（R3_SPEC 1）。"""
        return await self._send({
            "type": "reply_segment",
            "payload": {
                "turn_id": self._current_turn,
                "text": text, "audio_b64": audio_b64,
                "tone": tone, "portrait": portrait, "text_zh": text_zh,
            },
        })

    async def reply_end(self) -> bool:
        """R3 协议：回复结束（R3_SPEC 1）。"""
        return await self._send({
            "type": "reply_end",
            "payload": {"turn_id": self._current_turn},
        })

    async def reply_error(self, code: str = "tts_failed", recoverable: bool = True) -> bool:
        """R3 协议：回复失败（R3_SPEC 1）。"""
        return await self._send({
            "type": "reply_error",
            "payload": {"turn_id": self._current_turn, "code": code, "recoverable": recoverable},
        })

    async def _send(self, msg: dict) -> bool:
        # R3 协议统一信封：event_id + ts（R3_SPEC 1）
        if "event_id" not in msg:
            msg["event_id"] = uuid.uuid4().hex[:8]
        if "ts" not in msg:
            msg["ts"] = int(time.time())
        if self._client is None:
            return False
        try:
            await self._client.send(json.dumps(msg, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("send failed: %s", e)
            self._client = None
            return False

    # ---------- 接收处理 ----------
    async def _handle(self, ws: websockets.ServerConnection) -> None:
        self._client = ws
        logger.info("pet shell connected")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype in ("poke", "stream_talk"):
                    # R3 TTS 打断（R3_SPEC 1）：用户新互动 → 停止当前播放
                    await self.stop_speak()
                    self._current_turn = self._next_turn()  # R2：新 turn
                if mtype == "poke":
                    logger.info("poke received")
                    if self._agent is not None:
                        # 正式版：agent 生成一句互动（channel=tts 语音风格 + 表情标签）
                        try:
                            r = await self._call_agent("（用户戳了戳桌宠）")
                            await self.speak_result(r)
                        except Exception as e:
                            logger.warning("poke agent failed: %s", e)
                            await self.speak("嗯？叫我干嘛～")
                    else:
                        # PoC：无 agent 时写死文案
                        await self.speak("嗯？叫我干嘛～")
                elif mtype == "drag":
                    pass  # 拖拽由壳自己处理，核心无需响应（PoC）
                elif mtype == "stream_talk":
                    # 流式对话（DESIGN 4.13）：逐句推送 speak_chunk → speak_done
                    if self._agent is None:
                        await self.speak("（流式需要接入 agent）")
                        continue
                    try:
                        msg_text = str(msg.get("text") or "")
                        # R4_SPEC 4 忽略自愈：用户来消息 → 最近主动反馈标记 responded；
                        # 连续 2 条未响应 → 同源退避（note_ignored）
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
                        # R3 整段协议（与 speak 一致）：不再逐句 chunk
                        r = await self._call_agent(msg_text)
                        await self.speak_result(r)
                    except Exception as e:
                        logger.warning("stream_talk failed: %s", e)
                        await self.reply_error(code="reply_failed", recoverable=True)
                        await self.reply_segment(text="（这条回复没有完成，再说一次？）")
                        await self.reply_end()
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
                        "stt": {"base_url": cfg.get("stt", {}).get("base_url", ""),
                                "model": cfg.get("stt", {}).get("model", ""),
                                "language": cfg.get("stt", {}).get("language", "")},
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
                            "enabled", "max_per_day", "min_gap_minutes", "source_gap_minutes")},
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
                    for k in ("base_url", "model", "language"):
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
                    if "paused" in att:
                        cfg.setdefault("attention", {})["paused"] = att["paused"]
                    pro = d.get("proactive", {})
                    if "max_per_day" in pro:
                        cfg.setdefault("proactive", {})["max_per_day"] = pro["max_per_day"]
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
            self._client = None

    async def run(self) -> None:
        logger.info("pet server on ws://%s:%d", self.host, self.port)
        async with websockets.serve(self._handle, self.host, self.port):
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
                from veranima.core.ambient import ProactiveCandidate
                att = AttentionScheduler(llm=getattr(self._agent, "llm", None),
                                         config=self.attention_cfg)
                await asyncio.sleep(10)  # 启动后延迟（等核心就绪）
                logger.info("visual: 注意力循环启动（VISION_SPEC V1）")
                while True:
                    try:
                        for ev in await asyncio.to_thread(att.tick):
                            logger.info("visual: %s %s %s", ev.kind, ev.region,
                                        ev.note or ev.tag or "")
                            if self._agent is None:
                                continue
                            # QQ 通道互斥（沿用 R4_SPEC 1.3）：QQ 活跃跳过观察
                            qq_active = self._agent.activity.active("qq") if self._agent.activity else False
                            if qq_active:
                                logger.info("visual: QQ 活跃，跳过观察")
                                continue
                            if ev.kind in ("window_switch", "fixation_shift") and ev.tag:
                                # VISION_SPEC 5/7：观察只做短期情境（不写长期记忆——
                                # L3 禁止写 memories），观察结果 TTL 10min 过期不注入
                                try:
                                    # 观察缓存（同 window_category+region 120s 内不重复 L3）
                                    cache_key = f"{ev.kind}:{ev.tag}"
                                    if time.time() - self._obs_cache.get(cache_key, 0.0) < 120:
                                        continue
                                    summary, category = await self._observe_event(att, ev)
                                    if not summary:
                                        continue
                                    self._obs_cache[cache_key] = time.time()
                                    logger.info("visual: 观察 %s [%s]", summary[:40], category)
                                    # 共同经历匹配：观察 summary 检索 episodic（短期情境联想）
                                    matched = await asyncio.to_thread(
                                        self._agent._visual_match_episode, summary[:20])
                                    # R4：注意力只产候选（R4_SPEC 1——禁止直接 speak）
                                    cand = ProactiveCandidate(
                                        source="attention",
                                        reason=f"看到用户在{ev.tag}",
                                        relevance=0.7, urgency=0.4,
                                        intent="bridge",
                                        context={
                                            "tag": ev.tag,
                                            "matched_episode": matched,
                                        },
                                    )
                                    decision = self._agent.gate.decide(
                                        cand,
                                        scene=self._agent.scene_lock.current(),
                                        other_channel_active=self._agent.activity.blocking("pet"),
                                    )
                                    if not decision.allow:
                                        logger.debug("visual: 主动被闸门拦截 %s", decision.reason)
                                        continue
                                    proactive, ja = await asyncio.to_thread(
                                        self._agent.proactive_from_visual, ev.tag)
                                    if proactive:
                                        self._agent.gate.commit(cand)
                                        logger.info("visual: 联想主动发起: %s", proactive[:60])
                                        # R4_SPEC 4：反馈记录（用户是否回应由 stream_talk 更新）
                                        try:
                                            self._agent.memory.record_proactive_feedback(source="attention")
                                        except Exception as e:
                                            logger.debug("feedback record failed: %s", e)
                                        await self.speak(proactive, tts_text=ja or None)
                                except Exception as e:
                                    logger.warning("visual observe inject failed: %s", e)
                    except Exception as e:
                        logger.warning("visual tick failed: %s", e)
                    await asyncio.sleep(0.5)  # tick 内部自管理全局快照节奏
            asyncio.create_task(_visual_loop())
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
    srv.connect_agent(create_agent(cfg))
    logger.info("agent connected (character_card=%s)", cfg.get("character_card", ""))
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

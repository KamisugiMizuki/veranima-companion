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
                            await self.speak(r.reply, tags=[r.portrait] if r.portrait else None, tts_text=r.ja_text or None)
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
                        # R3 整段协议（与 speak 一致）：不再逐句 chunk
                        r = await self._call_agent(msg_text)
                        await self.speak(
                            r.reply,
                            tags=[r.portrait] if r.portrait else None,
                            tts_text=r.ja_text or None,
                        )
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
                    await self._send({"type": "config", "id": msg.get("id"), "data": {
                        "llm": {"base_url": llm.get("base_url", ""), "model": llm.get("model", ""),
                                "temperature": llm.get("temperature", 0.8), "api_key": masked},
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
                    }})
                elif mtype == "save_config":
                    # 设置窗口保存：白名单字段更新（全字段——之前只处理 llm/qq.allowed，
                    # character_card/tts/stt 等被静默丢弃，设置页形同虚设）
                    cfg = load_config()
                    d = msg.get("data", {})
                    llm = d.get("llm", {})
                    for k in ("base_url", "model", "temperature"):
                        if k in llm:
                            cfg.setdefault("llm", {})[k] = llm[k]
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
                                # 观察注入 episodic 记忆（短期情境；主动与否由 R4 闸门裁决）
                                try:
                                    self._agent.memory.store(
                                        "episodic",
                                        f"[屏幕观察] {ev.note}（{ev.tag}）",
                                        importance=0.5, confidence=0.7,
                                        provenance="visual-attention",
                                        category="screen",
                                    )
                                    logger.info("visual: 观察注入记忆 tag=%s", ev.tag)
                                    # R4：注意力只产候选（R4_SPEC 1——禁止直接 speak），
                                    # 必须带 matched_episode 且有共同经历，过 ProactiveGate
                                    cand = ProactiveCandidate(
                                        source="attention",
                                        reason=f"看到用户在{ev.tag}",
                                        relevance=0.7, urgency=0.4,
                                        intent="bridge",
                                        context={
                                            "tag": ev.tag,
                                            "matched_episode": self._agent._visual_match_episode(ev.tag),
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

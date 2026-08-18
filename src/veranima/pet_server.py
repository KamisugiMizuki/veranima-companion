"""桌宠核心 WS 服务端（M3_SPEC 1 进程架构 A + 3 通信协议）。

最小实现（M3b PoC）：
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
        self._tts = None    # TTSClient（可选；未配置则桌宠只显气泡不发声）
        self._presence_was_absent = False  # M3 衔接：在场转变检测

    def connect_agent(self, agent) -> None:
        """接入 Agent（正式版：poke/speak 走 agent.handle(channel='tts')）。"""
        self._agent = agent

    def connect_tts(self, tts) -> None:
        """接入 TTS（远程/本地统一 OpenAI 兼容接口；未配置则跳过合成）。"""
        self._tts = tts

    async def tick_presence(self) -> bool:
        """M3 无缝衔接（DESIGN 4.8）：L0 在场检测 → absent→present 转变 → 衔接语。

        返回是否发送了衔接语。无 agent / 用户仍在场 / 从未 absent 不触发。
        """
        from veranima.core.presence import presence
        if self._agent is None:
            return False
        now_present = presence()
        if now_present and self._presence_was_absent:
            self._presence_was_absent = False
            try:
                msg = await asyncio.to_thread(self._agent.seamless_greeting)
                if msg:
                    await self.speak(msg)
                    return True
            except Exception as e:
                logger.warning("seamless greeting failed: %s", e)
                return False
        if not now_present:
            self._presence_was_absent = True
        return False

    # ---------- 对外发送 ----------
    async def speak(self, text: str, tags: list | None = None, tts_text: str | None = None) -> bool:
        """推送回复（逐句合成+播放，M5 性能优化：第一句 ~3s 出声，后续边播边生成）。

        tts_text 指定时用它合成音频（M5 双语：ja 配音 / zh 显示）。
        句子拆分：TTS 用句读（。！？…），显示用原文对应句——拆不出就整段降级。
        """
        import base64

        speak_text = (tts_text or text).strip()
        if self._tts is None or not speak_text:
            # 无 TTS：一次性纯气泡
            return await self._send({"type": "speak", "text": text, "tags": tags or []})

        # 逐句：合成一句 → 立即推送（AR 自回归是串行瓶颈，但播放可以与生成重叠）
        sentences = _split_sentences(speak_text) or [speak_text]
        display_sentences = _split_sentences(text) or [text]
        ok = True
        for i, sent in enumerate(sentences):
            disp = display_sentences[i] if i < len(display_sentences) else (text if i == 0 else "")
            msg: dict = {"type": "speak", "text": disp or sent, "tags": tags or []}
            if tts_text:
                msg["text_zh"] = disp or text  # 双语：气泡显示中文
            try:
                audio = await asyncio.to_thread(self._tts.synthesize, sent)
                if audio:
                    msg["audio_b64"] = base64.b64encode(audio).decode()
            except Exception as e:
                logger.warning("tts synthesize failed (bubble only): %s", e)
            ok = await self._send(msg) and ok
        return ok

    async def speak_chunk(self, text: str) -> bool:
        """流式分片推送（DESIGN 4.13 打字机）。"""
        return await self._send({"type": "speak_chunk", "text": text})

    async def speak_done(self) -> bool:
        return await self._send({"type": "speak_done"})

    async def bubble(self, text: str) -> bool:
        return await self._send({"type": "bubble", "text": text})

    async def push_state(self, **extra) -> bool:
        return await self._send({"type": "state", **extra})

    async def stop_speak(self) -> bool:
        return await self._send({"type": "stop_speak"})

    async def _send(self, msg: dict) -> bool:
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
                    # M3 3.2 TTS 打断：用户新互动 → 停止当前播放
                    await self.stop_speak()
                if mtype == "poke":
                    logger.info("poke received")
                    if self._agent is not None:
                        # 正式版：agent 生成一句互动（channel=tts 语音风格 + 表情标签）
                        try:
                            r = await asyncio.to_thread(self._agent.handle, "（用户戳了戳桌宠）", channel="tts")
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
                        # 构建消息 → 流式生成（agent 的 messages 由 handle 内部构建；
                        # PoC：直接调 llm.stream_chat 需要消息列表——走 agent 的简化路径：
                        # 用一次性 handle 拿完整回复再按句推（保证与 agent 状态一致）
                        r = await asyncio.to_thread(self._agent.handle, msg_text, channel="tts")
                        for sent in _split_sentences(r.reply):
                            await self.speak_chunk(sent)
                        await self.speak_done()
                        # M5 双语：日语台词合成音频播放（显示已流式，音频补发）
                        if r.ja_text:
                            await self.speak(r.reply, tts_text=r.ja_text)
                    except Exception as e:
                        logger.warning("stream_talk failed: %s", e)
                        await self.speak_done()
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
            # M3 无缝衔接：30s 一次 L0 在场检测（absent→present → 衔接语）
            async def _presence_loop():
                while True:
                    try:
                        await self.tick_presence()
                    except Exception as e:
                        logger.warning("presence tick failed: %s", e)
                    await asyncio.sleep(30)
            asyncio.create_task(_presence_loop())

            # M4 视觉调度（M4_SPEC 1.2/1.3）：三态 + 像素差异 + L3 观察；QQ 活跃时降频
            async def _visual_loop():
                from veranima.core.vision import VisualAttention
                va = VisualAttention()
                await asyncio.sleep(10)  # 启动后延迟（等核心就绪）
                while True:
                    try:
                        va.tick(presence=True)
                        if va.state == "wander":
                            await asyncio.sleep(30)
                            continue
                        changed = await asyncio.to_thread(va.significant_change)
                        if changed and self._agent is not None:
                            # L3：远程多模态理解屏幕（QQ 活跃时跳过——通道互斥）
                            qq_active = self._agent.activity.active("qq") if self._agent.activity else False
                            if not qq_active:
                                await asyncio.to_thread(va.observe_screen, self._agent.llm)
                    except Exception as e:
                        logger.warning("visual tick failed: %s", e)
                    await asyncio.sleep(va.interval())
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

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

logger = logging.getLogger("veranima.pet_server")

PORT = 8765


class PetServer:
    """桌宠 WS 服务：维护单个客户端连接，收发协议消息。"""

    def __init__(self, host: str = "127.0.0.1", port: int = PORT) -> None:
        self.host = host
        self.port = port
        self._client: websockets.ServerConnection | None = None
        self._agent = None  # 后续接 Agent；PoC 阶段 None

    def connect_agent(self, agent) -> None:
        """接入 Agent（正式版：poke/speak 走 agent.handle(channel='tts')）。"""
        self._agent = agent

    # ---------- 对外发送 ----------
    async def speak(self, text: str, tags: list | None = None) -> bool:
        return await self._send({"type": "speak", "text": text, "tags": tags or []})

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
                if mtype == "poke":
                    logger.info("poke received")
                    if self._agent is not None:
                        # 正式版：agent 生成一句互动（channel=tts 语音风格）
                        try:
                            r = await asyncio.to_thread(self._agent.handle, "（用户戳了戳桌宠）", channel="tts")
                            await self.speak(r.reply)
                        except Exception as e:
                            logger.warning("poke agent failed: %s", e)
                            await self.speak("嗯？叫我干嘛～")
                    else:
                        # PoC：无 agent 时写死文案
                        await self.speak("嗯？叫我干嘛～")
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
                    await self._send({"type": "config", "id": msg.get("id"), "data": {
                        "llm": {"base_url": llm.get("base_url", ""), "model": llm.get("model", ""),
                                "api_key": masked},
                        "qq": {"allowed": cfg.get("allowed_qq", [])},
                    }})
                elif mtype == "save_config":
                    # 设置窗口保存：只允许更新白名单字段
                    cfg = load_config()
                    d = msg.get("data", {})
                    llm = d.get("llm", {})
                    if "base_url" in llm:
                        cfg.setdefault("llm", {})["base_url"] = llm["base_url"]
                    if "model" in llm:
                        cfg.setdefault("llm", {})["model"] = llm["model"]
                    if "allowed" in d.get("qq", {}):
                        cfg["allowed_qq"] = d["qq"]["allowed"]
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
            await asyncio.Future()  # 常驻


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Veranima 桌宠核心 WS 服务")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    asyncio.run(PetServer(port=args.port).run())


if __name__ == "__main__":
    main()

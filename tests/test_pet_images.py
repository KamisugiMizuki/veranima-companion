"""桌宠图片消息协议契约（文本检查 + 核心 WS 行为）。"""
from __future__ import annotations

import asyncio
import json

from veranima.pet_server import PetServer


class Client:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))


class Memory:
    def recent_proactive_feedback(self, limit=3): return []
    def record_proactive_feedback(self, **kwargs): pass


class Gate:
    def note_responded(self, source): pass


class Agent:
    def __init__(self):
        self.memory = Memory()
        self.gate = Gate()
        self.seen = None

    def handle(self, text, images=None, channel="tts"):
        self.seen = (text, images, channel)
        return type("R", (), {"reply": "收到图片", "reply_obj": None, "portrait": "", "tone": "", "ja_text": ""})()


def test_pet_stream_talk_passes_images_to_same_agent():
    async def run():
        server = PetServer()
        client = Client()
        agent = Agent()
        server._client = client
        server.connect_agent(agent)
        await server._run_stream_talk(
            "看这个", 1, "req-1", client,
            images=["data:image/png;base64, iVBORw0KGgo=".replace(" ", "")],
        )
        assert agent.seen[0] == "看这个"
        assert agent.seen[1][0].startswith("data:image/png;base64,")
        assert agent.seen[2] == "tts"

    asyncio.run(run())


def test_pet_server_ws_accepts_four_max_size_images(monkeypatch):
    import veranima.pet_server as module

    captured = {}

    class StopAtEnter:
        async def __aenter__(self):
            raise RuntimeError("captured")
        async def __aexit__(self, *args):
            return False

    def serve(*args, **kwargs):
        captured.update(kwargs)
        return StopAtEnter()

    monkeypatch.setattr(module.websockets, "serve", serve)
    try:
        asyncio.run(PetServer().run())
    except RuntimeError as exc:
        assert str(exc) == "captured"

    assert captured["max_size"] >= 64 * 1024 * 1024
    assert captured["origins"] == [None]


def test_pet_server_real_ws_accepts_frame_above_websockets_default():
    import contextlib
    import socket
    import websockets

    async def run():
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        server = PetServer(port=port)
        task = asyncio.create_task(server.run())
        try:
            rejected = False
            for _ in range(50):
                try:
                    evil = await websockets.connect(
                        f"ws://127.0.0.1:{port}", origin="https://evil.example",
                    )
                except OSError:
                    await asyncio.sleep(0.02)
                    continue
                except websockets.exceptions.InvalidHandshake:
                    rejected = True
                    break
                else:
                    await evil.close()
                    break
            assert rejected
            ws = None
            for _ in range(50):
                try:
                    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
                    break
                except OSError:
                    await asyncio.sleep(0.02)
            assert ws is not None
            async with ws:
                await ws.send(json.dumps({"type": "ping", "padding": "x" * (2 * 1024 * 1024)}))
                assert json.loads(await asyncio.wait_for(ws.recv(), timeout=2))["type"] == "pong"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_pet_protocol_mentions_image_payload():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    main = (root / "pet/main.js").read_text(encoding="utf-8")
    renderer = (root / "pet/chat-renderer.js").read_text(encoding="utf-8")
    html = (root / "pet/chat.html").read_text(encoding="utf-8")
    assert "clipboardData" in renderer
    assert "images" in main
    assert "imagePreview" in html

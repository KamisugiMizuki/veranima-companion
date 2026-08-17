"""TTS 客户端测试（OpenAI 兼容 /v1/audio/speech，远程/本地统一）。"""
import json

import pytest

from veranima.tts.client import TTSClient, TTSUnavailableError


def test_tts_requires_base_url():
    """未配置 base_url → 不可用。"""
    c = TTSClient({})
    assert c.is_available() is False
    with pytest.raises(TTSUnavailableError):
        c.synthesize("你好")


def test_tts_synthesize_success(monkeypatch):
    """synthesize 调 /v1/audio/speech 返回音频 bytes。"""
    import httpx
    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass
        @property
        def content(self):
            return b"RIFF fake-wav"

    class FakeClient:
        def __init__(self, timeout=None):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, json=None, headers=None):
            calls["url"] = url
            calls["json"] = json
            calls["headers"] = headers
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    c = TTSClient({"base_url": "http://127.0.0.1:9880/v1", "model": "qwen-tts", "voice": "zima"})
    assert c.is_available() is True
    audio = c.synthesize("你好，今天怎么样？")
    assert audio == b"RIFF fake-wav"
    assert calls["url"] == "http://127.0.0.1:9880/v1/audio/speech"
    assert calls["json"]["input"] == "你好，今天怎么样？"
    assert calls["json"]["voice"] == "zima"
    assert calls["json"]["response_format"] == "wav"


def test_tts_http_error(monkeypatch):
    """4xx → TTSUnavailableError。"""
    import httpx

    class FakeResp:
        def raise_for_status(self):
            raise httpx.HTTPStatusError("400", request=None, response=None)
        @property
        def text(self):
            return "bad request"

    class FakeClient:
        def __init__(self, timeout=None):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, json=None, headers=None):
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    c = TTSClient({"base_url": "http://x/v1"})
    with pytest.raises(TTSUnavailableError):
        c.synthesize("hi")


def test_pet_server_speak_with_tts(tmp_path):
    """PetServer.connect_tts 后 speak 携带 audio_b64（端到端协议）。"""
    import asyncio
    import websockets

    from veranima.pet_server import PetServer

    class FakeTTS:
        def synthesize(self, text):
            return b"RIFF fake-audio"

    port = _free_port()

    async def scenario():
        srv = PetServer(port=port)
        srv.connect_tts(FakeTTS())
        server = await websockets.serve(srv._handle, "127.0.0.1", port)
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            ok = await srv.speak("你好呀")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert ok is True
            assert msg["type"] == "speak"
            assert msg["text"] == "你好呀"
            assert msg["audio_b64"] == "UklGRiBmYWtlLWF1ZGlv"  # base64(b"RIFF fake-audio")
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())


def test_pet_server_speak_without_tts(tmp_path):
    """未接 TTS → speak 不带 audio_b64（纯气泡降级）。"""
    import asyncio
    import websockets

    from veranima.pet_server import PetServer

    port = _free_port()

    async def scenario():
        srv = PetServer(port=port)
        server = await websockets.serve(srv._handle, "127.0.0.1", port)
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await srv.speak("只有气泡")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg["type"] == "speak"
            assert "audio_b64" not in msg
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())


# ---------- TTS 服务（FastAPI /v1/audio/speech） ----------

def test_tts_server_health():
    """/health 可用（不加载模型）。"""
    from fastapi.testclient import TestClient
    from veranima.tts.server import create_app
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_tts_server_missing_input():
    """缺 input → 400。"""
    from fastapi.testclient import TestClient
    from veranima.tts.server import create_app
    client = TestClient(create_app())
    r = client.post("/v1/audio/speech", json={"model": "x"})
    assert r.status_code == 400


def test_tts_server_synthesize_mock(monkeypatch):
    """synthesize mock 音频 → 200 audio/wav。"""
    from fastapi.testclient import TestClient
    from veranima.tts import server as tts_server
    from veranima.tts.server import create_app
    monkeypatch.setattr(tts_server, "synthesize", lambda text, voice="alloy": b"RIFF mock-wav")
    client = TestClient(create_app())
    r = client.post("/v1/audio/speech", json={"model": "qwen-tts", "input": "你好", "response_format": "wav"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF mock-wav"


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

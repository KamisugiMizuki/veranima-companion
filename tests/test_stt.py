"""STT 客户端测试（OpenAI 兼容 /v1/audio/transcriptions）。"""
import json

import pytest

from veranima.stt.client import STTClient, STTUnavailableError


def test_stt_requires_base_url():
    """未配置 base_url → 返回空串（提示但不报错，调用方降级）。"""
    c = STTClient({})
    assert c.is_available() is False
    assert c.transcribe(b"RIFF....") == ""


def test_stt_transcribe_mock(monkeypatch):
    """mock 返回识别文本 → transcribe 解析正确（multipart 请求）。"""
    import httpx

    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "今天天气不错"}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        captured["headers"] = kwargs.get("headers")
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    c = STTClient({"base_url": "http://127.0.0.1:9999/v1", "model": "whisper-1"})
    text = c.transcribe(b"fake-audio-bytes", filename="voice.webm")
    assert text == "今天天气不错"
    assert captured["url"] == "http://127.0.0.1:9999/v1/audio/transcriptions"  # 不重复 /v1
    assert captured["data"]["model"] == "whisper-1"
    assert "file" in captured["files"]
    assert captured["files"]["file"][2] == "audio/webm"


def test_stt_url_no_v1_suffix(monkeypatch):
    """base_url 不带 /v1 → 自动补全。"""
    import httpx
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "x"}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    c = STTClient({"base_url": "http://127.0.0.1:9999"})
    c.transcribe(b"x")
    assert captured["url"] == "http://127.0.0.1:9999/v1/audio/transcriptions"


def test_stt_language_param(monkeypatch):
    """language 配置传入请求。"""
    import httpx
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "x"}

    def fake_post(self, url, **kwargs):
        captured["data"] = kwargs.get("data")
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    c = STTClient({"base_url": "http://x/v1", "language": "zh"})
    c.transcribe(b"x")
    assert captured["data"]["language"] == "zh"


def test_stt_http_error(monkeypatch):
    """HTTP 错误 → STTUnavailableError（配置了但服务故障仍报错）。"""
    import httpx

    class FakeResponse:
        status_code = 500
        text = "boom"

        def raise_for_status(self):
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def fake_post(self, url, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    c = STTClient({"base_url": "http://x/v1"})
    with pytest.raises(STTUnavailableError):
        c.transcribe(b"x")

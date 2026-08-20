"""SenseVoice STT 后端与 HTTP 契约。"""
from __future__ import annotations

import sys
import types

import pytest

from veranima.stt.sensevoice import SenseVoiceBackend, STTBackendError
from veranima.stt.server import create_app


def test_sensevoice_backend_passes_auto_language(monkeypatch, tmp_path):
    calls = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def generate(self, **kwargs):
            calls["generate"] = kwargs
            return [{"text": "中文 API そして English"}]

    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeModel))
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFFfake")
    backend = SenseVoiceBackend(tmp_path, device="cpu", language="auto")
    assert backend.transcribe(audio) == "中文 API そして English"
    assert calls["init"]["device"] == "cpu"
    assert calls["generate"]["language"] == "auto"
    assert calls["generate"]["use_itn"] is True


def test_sensevoice_empty_auto_result_uses_configured_language_fallback(monkeypatch, tmp_path):
    calls = []

    class FakeModel:
        def __init__(self, **kwargs):
            pass

        def generate(self, **kwargs):
            calls.append(kwargs["language"])
            return [{"text": "中文结果" if kwargs["language"] == "zh" else ""}]

    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeModel))
    backend = SenseVoiceBackend(tmp_path, language="auto", language_priority=["zh", "en", "ja"])

    assert backend.transcribe(tmp_path / "sample.wav") == "中文结果"
    assert calls == ["auto", "zh"]


def test_sensevoice_uses_vad_per_segment_for_code_switch(monkeypatch, tmp_path):
    calls = {}
    vad = tmp_path / "vad"
    vad.mkdir()

    class FakeModel:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def generate(self, **kwargs):
            calls["generate"] = kwargs
            return [{"text": "中文 English 日本語"}]

    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeModel))
    backend = SenseVoiceBackend(tmp_path, vad_model_path=vad)

    assert backend.transcribe(tmp_path / "mixed.wav") == "中文 English 日本語"
    assert calls["init"]["vad_model"] == str(vad)
    assert calls["init"]["vad_kwargs"]["max_single_segment_time"] == 10_000
    assert calls["generate"]["merge_vad"] is False
    assert calls["generate"]["batch_size_s"] == 60


def test_stt_http_contract_accepts_audio_and_returns_metadata(tmp_path):
    class FakeBackend:
        loaded = False

        def transcribe(self, path, *, language):
            assert path.exists()
            assert language == "auto"
            return "中文 English 日本語"

    app = create_app(FakeBackend(), max_audio_bytes=100)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("voice.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
            data={"model": "sensevoice-small", "language": "auto"},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "中文 English 日本語"
    assert response.json()["language"] == "auto"
    assert response.json()["provider"] == "sensevoice"


def test_stt_http_rejects_oversized_audio():
    class FakeBackend:
        loaded = False

        def transcribe(self, path, *, language):
            raise AssertionError("backend must not receive oversized audio")

    app = create_app(FakeBackend(), max_audio_bytes=3)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("voice.wav", b"1234", "audio/wav")},
        )
    assert response.status_code == 413


def test_stt_http_rejects_browser_origin_and_invalid_container():
    class FakeBackend:
        loaded = False
        calls = 0

        def transcribe(self, path, *, language):
            self.calls += 1
            return "must not run"

    backend = FakeBackend()
    from fastapi.testclient import TestClient
    with TestClient(create_app(backend)) as client:
        cross_origin = client.post(
            "/v1/audio/transcriptions",
            headers={"Origin": "https://evil.example"},
            files={"file": ("voice.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        )
        bad_container = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("voice.webm", b"not-an-audio-container", "audio/webm")},
        )

    assert cross_origin.status_code == 403
    assert bad_container.status_code == 415
    assert backend.calls == 0


def test_stt_http_uses_mime_suffix_and_offloads_backend(monkeypatch, tmp_path):
    import veranima.stt.server as server

    calls = []
    suffixes = []
    real_mkstemp = server.tempfile.mkstemp

    async def fake_to_thread(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    def fake_mkstemp(*args, **kwargs):
        suffixes.append(kwargs.get("suffix"))
        kwargs["dir"] = tmp_path
        return real_mkstemp(*args, **kwargs)

    class FakeBackend:
        loaded = True
        def transcribe(self, path, *, language):
            return "ok"

    monkeypatch.setattr(server.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server.tempfile, "mkstemp", fake_mkstemp)
    from fastapi.testclient import TestClient
    with TestClient(server.create_app(FakeBackend())) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("untrusted.exe", b"\x1aE\xdf\xa3webm", "audio/webm")},
        )
    assert response.status_code == 200
    assert suffixes == [".webm"]
    assert len(calls) == 1
    assert calls[0].__func__ is FakeBackend.transcribe


def test_sensevoice_load_failure_is_backend_error(monkeypatch, tmp_path):
    class BrokenModel:
        def __init__(self, **kwargs):
            raise RuntimeError("broken model")

    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=BrokenModel))
    backend = SenseVoiceBackend(tmp_path)
    with pytest.raises(STTBackendError, match="加载失败"):
        backend.load()


def test_stt_server_main_uses_existing_config(monkeypatch):
    import sys
    import veranima.config as config_module
    import veranima.stt.server as server

    captured = {}
    monkeypatch.setattr(config_module, "load_config", lambda: {"stt": {
        "model_path": "custom/model", "device": "cuda", "language": "auto",
        "language_priority": ["zh", "en", "ja"],
        "max_audio_bytes": 1234,
    }})
    monkeypatch.setattr(server, "SenseVoiceBackend", lambda path, **kw: captured.update(path=path, **kw) or object())
    monkeypatch.setattr(server, "create_app", lambda backend, **kw: captured.update(app=kw) or object())
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: captured.update(run=kw))
    monkeypatch.setattr(sys, "argv", ["stt-server"])

    server.main()

    assert captured["path"] == "custom/model"
    assert captured["device"] == "cuda" and captured["language"] == "auto"
    assert captured["vad_model_path"].endswith("speech_fsmn_vad_zh-cn-16k-common-pytorch")
    assert captured["language_priority"] == ["zh", "en", "ja"]
    assert captured["app"]["max_audio_bytes"] == 1234
    assert captured["run"]["port"] == 9890

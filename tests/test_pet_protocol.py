"""R3 桌宠协议测试（R3_SPEC 7）。

覆盖：事件信封（event_id/ts）、reply_start/segment/end/error/cancelled 序列、
TTS 失败保留文字、turn_id 携带、push_state payload 结构。
"""
from __future__ import annotations

import asyncio
import json

from veranima.pet_server import PetServer


def _make_srv() -> tuple[PetServer, list[dict]]:
    srv = PetServer()
    sent: list[dict] = []

    class FakeClient:
        async def send(self, data):
            sent.append(json.loads(data))

    srv._client = FakeClient()  # 替换底层连接（保留 _send 信封逻辑）
    return srv, sent


def test_event_envelope():
    """R3_SPEC 1：事件统一带 event_id + ts。"""
    srv, sent = _make_srv()
    asyncio.run(srv._send({"type": "ping"}))
    assert sent[0]["event_id"]
    assert sent[0]["ts"] > 0


def test_push_state_payload():
    """R3_SPEC 1：state 事件 payload 结构。"""
    srv, sent = _make_srv()
    asyncio.run(srv.push_state(status="online", character="Yuki"))
    m = sent[0]
    assert m["type"] == "state"
    assert m["payload"]["status"] == "online"
    assert m["payload"]["character"] == "Yuki"
    assert "turn_id" in m["payload"]


def test_reply_sequence():
    """R3_SPEC 1：reply_start → reply_segment → reply_end。"""
    srv, sent = _make_srv()
    srv._current_turn = 7
    asyncio.run(srv.reply_start())
    asyncio.run(srv.reply_segment(text="你好", audio_b64="AQA=", portrait="微笑", text_zh=""))
    asyncio.run(srv.reply_end())
    types = [m["type"] for m in sent]
    assert types == ["reply_start", "reply_segment", "reply_end"]
    seg = sent[1]["payload"]
    assert seg["turn_id"] == 7
    assert seg["text"] == "你好"
    assert seg["portrait"] == "微笑"


def test_reply_cancelled():
    """R3_SPEC 1：stop_speak → reply_cancelled 带 turn_id。"""
    srv, sent = _make_srv()
    srv._current_turn = 3
    asyncio.run(srv.stop_speak())
    assert sent[0]["type"] == "reply_cancelled"
    assert sent[0]["payload"]["turn_id"] == 3


def test_speak_no_tts_bubble_only():
    """无 TTS：纯气泡序列（无音频），仍走 reply_* 协议。"""
    srv, sent = _make_srv()
    srv._tts = None
    asyncio.run(srv.speak("你好呀"))
    types = [m["type"] for m in sent]
    assert types == ["reply_start", "reply_segment", "reply_end"]
    seg = sent[1]["payload"]
    assert seg["text"] == "你好呀"
    assert seg["audio_b64"] == ""


def test_speak_tts_failure_keeps_text():
    """TTS 失败：reply_error（可恢复）+ 文字保留（R3_SPEC 5）。"""
    srv, sent = _make_srv()

    class BoomTTS:
        def synthesize(self, text):
            raise RuntimeError("tts down")

    srv._tts = BoomTTS()
    srv._bilingual = False
    asyncio.run(srv.speak("这句话必须保留"))
    types = [m["type"] for m in sent]
    assert "reply_error" in types
    err = next(m for m in sent if m["type"] == "reply_error")
    assert err["payload"]["code"] == "tts_failed"
    assert err["payload"]["recoverable"] is True
    seg = next(m for m in sent if m["type"] == "reply_segment")
    assert seg["payload"]["text"] == "这句话必须保留"  # 文字不清空


def test_speak_bilingual_ja():
    """双语：ja 送 TTS（text=ja），text_zh 显示。"""
    srv, sent = _make_srv()

    class FakeTTS:
        def synthesize(self, text):
            assert text == "こんにちは"
            return b"WAVDATA"

    srv._tts = FakeTTS()
    srv._bilingual = True
    asyncio.run(srv.speak("你好", tts_text="こんにちは"))
    seg = next(m for m in sent if m["type"] == "reply_segment")
    assert seg["payload"]["text"] == "你好"
    assert seg["payload"]["text_zh"] == "你好"
    assert seg["payload"]["audio_b64"]


def test_speak_bilingual_missing_ja_no_tts():
    """双语缺 ja：不送日语模型（R2_SPEC 2 防御），纯气泡。"""
    srv, sent = _make_srv()

    class ShouldNotCallTTS:
        def synthesize(self, text):
            raise AssertionError("TTS should not be called")

    srv._tts = ShouldNotCallTTS()
    srv._bilingual = True
    asyncio.run(srv.speak("只有中文", tts_text=None))
    seg = next(m for m in sent if m["type"] == "reply_segment")
    assert seg["payload"]["audio_b64"] == ""


def test_turn_id_increment():
    """R2_SPEC 5：新输入分配新 turn。"""
    srv, _ = _make_srv()
    assert srv._next_turn() == 1
    assert srv._next_turn() == 2

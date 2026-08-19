"""PetServer 完整 Reply/TTS 分段消费测试。"""
from __future__ import annotations

import asyncio
import json

from veranima.core.reply import Reply, ReplySegment
from veranima.pet_server import PetServer


def _srv():
    srv = PetServer()
    sent = []

    class Client:
        async def send(self, data):
            sent.append(json.loads(data))

    srv._client = Client()
    return srv, sent


def test_speak_reply_consumes_all_segments_and_metadata():
    srv, sent = _srv()

    class TTS:
        def __init__(self): self.calls = []
        def synthesize(self, text):
            self.calls.append(text)
            return b"wav"

    tts = TTS()
    srv._tts = tts
    reply = Reply(segments=[
        ReplySegment(text="第一句", tone="温柔", portrait="闲置"),
        ReplySegment(text="第二句", tone="认真", portrait="微笑"),
    ])
    asyncio.run(srv.speak_reply(reply))
    segs = [m["payload"] for m in sent if m["type"] == "reply_segment"]
    assert [s["text"] for s in segs] == ["第一句", "第二句"]
    assert [s["tone"] for s in segs] == ["温柔", "认真"]
    assert [s["portrait"] for s in segs] == ["闲置", "微笑"]
    assert tts.calls == ["第一句", "第二句"]


def test_speak_reply_missing_bilingual_ja_skips_tts():
    srv, sent = _srv()

    class TTS:
        def synthesize(self, text):
            raise AssertionError("missing ja must not call TTS")

    srv._tts = TTS()
    reply = Reply(segments=[ReplySegment(text="中文显示", translation="中文显示", suppress_tts=True)])
    asyncio.run(srv.speak_reply(reply))
    seg = next(m["payload"] for m in sent if m["type"] == "reply_segment")
    assert seg["text"] == "中文显示"
    assert seg["audio_b64"] == ""

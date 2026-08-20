"""图片跨模块消费契约。"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from aiocqhttp import Message
from PIL import Image

from veranima.adapters.qq import QQAdapter
from veranima.core.agent import TurnResult
from veranima.core.image_payload import ImagePayloadError, make_image_payload, payload_from_data_url
from veranima.core.stickers import StickerLibrary


def png_bytes() -> bytes:
    import io
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (220, 20, 20)).save(buf, "PNG")
    return buf.getvalue()


def test_image_payload_rejects_mismatched_magic():
    with pytest.raises(ImagePayloadError):
        make_image_payload(b"not-png", content_type="image/png")
    with pytest.raises(ImagePayloadError):
        make_image_payload(b"\x89PNG\r\n\x1a\n", content_type="image/png")


def test_data_url_size_is_rejected_before_base64_decode(monkeypatch):
    import veranima.core.image_payload as module

    monkeypatch.setattr(module, "MAX_IMAGE_BYTES", 3)
    monkeypatch.setattr(module.base64, "b64decode", lambda *_a, **_k: pytest.fail("decoder was called"))
    with pytest.raises(ImagePayloadError, match="exceeds"):
        payload_from_data_url("data:image/png;base64,AAAAAAAA")


def test_qq_file_path_is_resolved_and_sent_to_agent(tmp_path, monkeypatch):
    class Agent:
        def __init__(self):
            self.seen = None
            self.memory = type("M", (), {"recent_proactive_feedback": lambda *_: [], "record_proactive_feedback": lambda *_a, **_k: None})()
            self.activity = type("A", (), {"touch": lambda *_: None})()
            self.gate = type("G", (), {})()
            self.state = type("S", (), {"attachment": .5})()
            self.card = None
        def handle(self, text, images=None, channel="im"):
            self.seen = (text, images, channel)
            return TurnResult(reply="看到了", energy=80, mood="平静")
    agent = Agent()
    adapter = QQAdapter(agent, allowed_qq=[1], quiet_hours=None)
    adapter.bot = type("B", (), {"send": lambda self, *a, **k: _done(), "send_private_msg": lambda *a, **k: _done()})()
    raw = png_bytes()
    local = tmp_path / "qq.png"
    local.write_bytes(raw)
    monkeypatch.setattr(adapter, "_resolve_local_image", lambda value: local)
    asyncio.run(adapter._handle_private({
        "user_id": 1,
        "message": Message("[CQ:image,file=qq.png]看图"),
    }))
    assert agent.seen[0] == "看图"
    assert agent.seen[2] == "im"
    assert agent.seen[1][0].startswith("data:image/png;base64,")


def test_dynamic_gif_is_understood_but_not_stored(tmp_path):
    import io
    frames = [Image.new("RGB", (8, 8), color) for color in ((255, 0, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)

    payload = make_image_payload(buf.getvalue(), content_type="image/gif", source="qq")
    lib = StickerLibrary(tmp_path / "stickers")
    assert payload.animated
    assert lib.add_payload(payload) is None
    assert len(lib) == 0


def test_dynamic_webp_is_understood_but_not_stored(tmp_path):
    import io
    frames = [Image.new("RGB", (8, 8), color) for color in ((0, 255, 0), (255, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:], duration=100, loop=0)

    payload = make_image_payload(buf.getvalue(), content_type="image/webp", source="qq")
    lib = StickerLibrary(tmp_path / "stickers")
    assert payload.animated
    assert lib.add_payload(payload) is None
    assert len(lib) == 0


def _done():
    async def done():
        return None
    return done()

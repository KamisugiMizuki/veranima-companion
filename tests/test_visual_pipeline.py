"""视觉注意力生产调用链行为测试。"""
from __future__ import annotations

import asyncio
import base64
import io
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image

from veranima.core.attention.events import AttentionEvent, Observation
from veranima.core.attention.scheduler import AttentionScheduler
from veranima.pet_server import PetServer


def test_color_crop_preserves_rgb(monkeypatch):
    from veranima.core.attention import perception

    image = Image.new("RGB", (100, 80), (240, 20, 10))
    monkeypatch.setattr(perception.ImageGrab, "grab", lambda: image.copy())
    monkeypatch.setattr(perception, "_CAN_CAPTURE", True)

    encoded = perception.grab_color_region((0.0, 0.0, 1.0, 1.0), crop_ratio=1.0)
    decoded = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")
    r, g, b = decoded.getpixel((0, 0))
    assert r > 200 and g < 50 and b < 50


def test_sensitive_window_blocks_before_capture(monkeypatch):
    from veranima.core.attention import perception

    calls = []
    monkeypatch.setattr(perception, "grab_gray_downsampled",
                        lambda scale=8: calls.append(1) or np.zeros((20, 30), dtype=np.uint8))
    att = AttentionScheduler(config={})
    att._foreground = lambda: "工商银行登录"

    events = att.tick()
    assert not calls
    assert len(events) == 1 and events[0].kind == "privacy_block"
    assert events[0].window_title == ""
    assert events[0].window_category == "sensitive"


def test_window_switch_never_observes(monkeypatch):
    server = PetServer()
    server._agent = object()
    calls = []

    async def fake_observe(att, ev):
        calls.append(ev.event_id)
        return Observation(event_id=ev.event_id, summary="x", category="coding", confidence=0.8)

    monkeypatch.setattr(server, "_observe_event", fake_observe)
    ev = AttentionEvent(kind="window_switch", note="Chrome", window_title="Chrome")
    assert asyncio.run(server._process_attention_event(SimpleNamespace(), ev)) is False
    assert calls == []


def test_attention_event_ttl_and_id_dedupe(monkeypatch):
    server = PetServer()
    server._agent = object()
    calls = []

    async def fake_observe(att, ev):
        calls.append(ev.event_id)
        return Observation(event_id=ev.event_id, category="unknown", confidence=0.0)

    monkeypatch.setattr(server, "_observe_event", fake_observe)
    expired = AttentionEvent(kind="fixation_shift", expires_at=time.time() - 1)
    assert asyncio.run(server._process_attention_event(SimpleNamespace(), expired)) is False
    assert calls == []

    fresh = AttentionEvent(kind="fixation_shift", window_category="coding")
    assert asyncio.run(server._process_attention_event(SimpleNamespace(), fresh)) is False
    assert asyncio.run(server._process_attention_event(SimpleNamespace(), fresh)) is False
    assert calls == [fresh.event_id]


def test_observation_uses_hybrid_recall_before_proactive(monkeypatch):
    server = PetServer()
    calls = {"recall": [], "proactive": [], "speak": []}
    memory_entry = SimpleNamespace(id=7, layer="episodic", confidence=0.9,
                                   content="上次用户调试代码到很晚")
    memory = SimpleNamespace(
        recall=lambda query, top_k=5: calls["recall"].append((query, top_k)) or [memory_entry],
        record_proactive_feedback=lambda **kwargs: None,
    )
    gate = SimpleNamespace(
        decide=lambda *args, **kwargs: SimpleNamespace(allow=True, reason="ok"),
        commit=lambda candidate: None,
    )
    agent = SimpleNamespace(
        activity=None, memory=memory, gate=gate,
        scene_lock=SimpleNamespace(current=lambda: "normal"),
        proactive_from_visual=lambda tag, matched: calls["proactive"].append((tag, matched)) or ("还在调代码？", ""),
    )
    server._agent = agent

    async def fake_observe(att, ev):
        return Observation(event_id=ev.event_id, summary="编辑器显示代码和终端",
                           category="coding", confidence=0.85)

    async def fake_speak(text, **kwargs):
        calls["speak"].append(text)
        return True

    monkeypatch.setattr(server, "_observe_event", fake_observe)
    monkeypatch.setattr(server, "speak", fake_speak)
    event = AttentionEvent(kind="fixation_shift", region=(0.1, 0.1, 0.4, 0.4),
                           window_category="coding")

    assert asyncio.run(server._process_attention_event(SimpleNamespace(), event)) is True
    assert calls["recall"] == [("coding 编辑器显示代码和终端", 5)]
    assert calls["proactive"] == [("coding", memory_entry.content)]
    assert calls["speak"] == ["还在调代码？"]

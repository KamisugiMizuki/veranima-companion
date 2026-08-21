"""R4 主动性闸门测试（R4_SPEC 6）。

覆盖：场景阻塞、通道独立间隔、静默时段、日上限、
视觉候选无共同经历不发送、commitment 可发、pause/resume。
"""
from __future__ import annotations

import time
import datetime

import pytest

from veranima.core.ambient import ProactiveCandidate, ProactiveGate

# 白天固定基准（避免 quiet hours [23,8] 干扰）
NOW = datetime.datetime(2026, 8, 19, 12, 0).timestamp()


def _cand(source: str = "shared_episode", relevance: float = 0.8,
          channel: str = "qq", **kw) -> ProactiveCandidate:
    base = dict(channel=channel, source=source, reason="test", relevance=relevance)
    base.update(kw)
    return ProactiveCandidate(**base)


def test_basic_allowed():
    gate = ProactiveGate(config={}, now=1_000_000)
    d = gate.decide(_cand())
    assert d.allow
    assert d.reason == "allowed"


def test_disabled():
    gate = ProactiveGate(config={"enabled": False}, now=1_000_000)
    assert not gate.decide(_cand()).allow


def test_paused_by_user():
    gate = ProactiveGate(config={}, now=1_000_000)
    gate.pause()
    assert not gate.decide(_cand()).allow
    gate.resume()
    assert gate.decide(_cand()).allow


def test_scene_blocked():
    gate = ProactiveGate(config={}, now=1_000_000)
    assert not gate.decide(_cand(), scene="busy").allow
    assert not gate.decide(_cand(), scene="away").allow
    assert gate.decide(_cand(), scene="normal").allow


def test_other_channel_active_does_not_block_channel_cooldown():
    gate = ProactiveGate(config={}, now=1_000_000)
    assert gate.decide(_cand(), other_channel_active=True).allow


def test_quiet_hours():
    # quiet_hours [23, 8]；now 用 datetime 可注入的固定时间（凌晨 3 点 = 3 点）
    three_am = datetime.datetime(2026, 8, 19, 3, 0).timestamp()
    gate = ProactiveGate(config={}, now=three_am)
    assert not gate.decide(_cand()).allow
    noon = NOW
    gate2 = ProactiveGate(config={}, now=noon)
    assert gate2.decide(_cand()).allow


def test_daily_cap():
    gate = ProactiveGate(config={}, now=NOW)
    # 两次使用不同来源，但只计算 QQ 自己的间隔和每日上限
    for src in ("shared_episode", "commitment"):
        assert gate.decide(_cand(source=src)).allow
        gate.commit(_cand(source=src))
        gate._now += 31 * 60  # 过 QQ 自己的 30min 间隔
    assert not gate.decide(_cand()).allow  # 默认 max_per_day=2


def test_each_channel_has_one_min_gap_only():
    gate = ProactiveGate(config={}, now=NOW)
    assert gate.decide(_cand()).allow
    gate.commit(_cand())
    # 自身通道的间隔未到，换来源也不能绕过
    gate._now = NOW + 60
    assert not gate.decide(_cand(source="commitment")).allow
    # 自身通道间隔到期，换来源不再有第二层冷却
    gate._now = NOW + 31 * 60
    assert gate.decide(_cand(source="commitment")).allow


def test_attention_without_memory_blocked():
    gate = ProactiveGate(config={}, now=1_000_000)
    cand = _cand(source="attention", relevance=0.7)
    assert not gate.decide(cand).allow  # 无 matched_episode
    cand2 = _cand(source="attention", relevance=0.7,
                  context={"tag": "游戏", "matched_episode": True})
    assert gate.decide(cand2).allow


def test_attention_low_relevance_blocked():
    gate = ProactiveGate(config={}, now=1_000_000)
    cand = _cand(source="attention", relevance=0.5,
                 context={"tag": "游戏", "matched_episode": True})
    assert not gate.decide(cand).allow  # relevance < 0.65


def test_ritual_needs_calendar_source():
    gate = ProactiveGate(config={}, now=1_000_000)
    assert not gate.decide(_cand(source="ritual")).allow
    cand = _cand(source="ritual", context={"calendar_source": "occasion"})
    assert gate.decide(cand).allow


def test_commitment_allowed():
    gate = ProactiveGate(config={}, now=1_000_000)
    assert gate.decide(_cand(source="commitment")).allow


def test_note_failure_no_commit():
    gate = ProactiveGate(config={}, now=1_000_000)
    cand = _cand()
    assert gate.decide(cand).allow
    gate.note_failure(cand)  # 生成失败不 commit
    assert gate.decide(cand).allow  # 仍可再试（未计冷却/日上限）


def test_channel_cooldowns_are_independent():
    gate = ProactiveGate(config={
        "channels": {
            "qq": {"min_gap_minutes": 120, "max_per_day": 2},
            "pet": {"min_gap_minutes": 30, "max_per_day": 2},
        },
    }, now=NOW)
    qq = _cand(channel="qq")
    pet = _cand(channel="pet", source="attention", context={"matched_episode": True})

    gate.commit(qq)

    assert not gate.decide(_cand(channel="qq", source="commitment")).allow
    assert gate.decide(pet).allow

    gate._now += 121 * 60
    gate.commit(pet)
    assert gate.decide(_cand(channel="qq", source="commitment")).allow


def test_channel_daily_caps_are_independent():
    gate = ProactiveGate(config={
        "channels": {
            "qq": {"min_gap_minutes": 0, "max_per_day": 1},
            "pet": {"min_gap_minutes": 0, "max_per_day": 1},
        },
    }, now=NOW)
    gate.commit(_cand(channel="qq"))

    assert not gate.decide(_cand(channel="qq", source="commitment")).allow
    assert gate.decide(_cand(channel="pet", source="attention",
                             context={"matched_episode": True})).allow


def test_legacy_gap_config_seeds_both_channels():
    gate = ProactiveGate(config={
        "min_gap_minutes": 45,
        "max_per_day": 3,
    }, now=NOW)

    assert gate.channel_config("qq")["min_gap_minutes"] == 45
    assert gate.channel_config("pet")["min_gap_minutes"] == 45
    assert gate.channel_config("qq")["max_per_day"] == 3

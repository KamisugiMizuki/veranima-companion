from __future__ import annotations

import datetime as dt

from veranima.core.ambient import ProactiveCandidate, ProactiveGate


def candidate(source: str, channel: str):
    return ProactiveCandidate(
        source=source, reason="有来源的日程素材", relevance=0.9,
        urgency=0.3, intent="share", context={"calendar_source": "virtual_schedule", "event_id": "e1"}, channel=channel,
    )


def test_virtual_schedule_candidates_use_independent_channel_gates():
    gate = ProactiveGate({"enabled": True, "quiet_hours_enabled": False, "channels": {
        "qq": {"enabled": True, "min_gap_minutes": 120, "max_per_day": 2},
        "pet": {"enabled": True, "min_gap_minutes": 30, "max_per_day": 2},
    }}, now=dt.datetime(2026, 8, 28, 12).timestamp())
    qq = candidate("virtual_schedule", "qq")
    pet = candidate("virtual_schedule", "pet")

    assert gate.decide(qq, now=dt.datetime(2026, 8, 28, 12).timestamp()).allow
    gate.commit(qq)
    assert gate.decide(qq, now=dt.datetime(2026, 8, 28, 12, minute=30).timestamp()).allow is False
    assert gate.decide(pet, now=dt.datetime(2026, 8, 28, 12, minute=30).timestamp()).allow


def test_virtual_schedule_candidate_without_anchor_is_rejected():
    gate = ProactiveGate({"enabled": True, "quiet_hours_enabled": False})
    item = ProactiveCandidate(
        source="virtual_schedule", reason="", relevance=0.9, urgency=0.3,
        intent="share", context={}, channel="qq",
    )

    assert gate.decide(item).allow is False


def test_all_proactive_candidates_are_blocked_while_character_sleeps():
    gate = ProactiveGate({"enabled": True, "quiet_hours_enabled": False})
    item = ProactiveCandidate(
        source="ritual", reason="calendar", relevance=1.0, urgency=0.5,
        intent="share", context={"calendar_source": "test"}, channel="qq",
    )

    assert gate.decide(item, character_sleeping=True).allow is False

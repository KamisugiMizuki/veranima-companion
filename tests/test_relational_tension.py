from __future__ import annotations

import datetime as dt

from veranima.core.tension import RelationalTension, RelationalTensionState, derive_band


def at(hour: int) -> dt.datetime:
    return dt.datetime(2026, 8, 22, hour, tzinfo=dt.timezone.utc)


def test_tension_event_is_bounded_and_deduplicated():
    tension = RelationalTension()

    first = tension.apply_event(
        event_type="unanswered_proactive", channel="qq", base_delta=10,
        reason="未回复", dedupe_key="expectation:1", occurred_at=at(0),
    )
    second = tension.apply_event(
        event_type="unanswered_proactive", channel="qq", base_delta=10,
        reason="重复", dedupe_key="expectation:1", occurred_at=at(0),
    )

    assert first.applied is True
    assert second.applied is False
    assert tension.state.value == 10
    assert tension.state.band == "calm"


def test_daily_negative_cap_and_floor():
    tension = RelationalTension()
    for i in range(5):
        tension.apply_event(
            event_type="terse_streak", channel="qq", base_delta=10,
            reason="短回复", dedupe_key=f"terse:{i}", occurred_at=at(0),
        )
    assert tension.state.value == 20

    tension.apply_event(
        event_type="repair", channel="qq", base_delta=-50,
        reason="修复", dedupe_key="repair:1", occurred_at=at(0),
    )
    assert tension.state.value == 0


def test_time_decay_uses_six_hour_steps_once():
    tension = RelationalTension()
    tension.apply_event(
        event_type="unanswered_proactive", channel="qq", base_delta=20,
        reason="未回复", dedupe_key="expectation:1", occurred_at=at(0),
    )

    assert tension.decay(now=at(5)) == 0
    assert tension.decay(now=at(12)) == 10
    assert tension.state.value == 10
    assert tension.decay(now=at(13)) == 0


def test_band_hysteresis():
    assert derive_band(21, "calm") == "guarded"
    assert derive_band(20, "guarded") == "guarded"
    assert derive_band(15, "guarded") == "calm"
    assert derive_band(81, "calm") == "high"
    assert derive_band(65, "high") == "high"


def test_snapshot_restore_keeps_tension_state():
    tension = RelationalTension()
    tension.apply_event(
        event_type="unanswered_proactive", channel="qq", base_delta=35,
        reason="未回复", dedupe_key="expectation:1", occurred_at=at(0),
    )

    restored = RelationalTension(RelationalTensionState.from_dict(tension.snapshot()))

    assert restored.state.value == 20  # daily cap applies before restore is observed
    assert restored.band == tension.band


def test_snapshot_restore_rebuilds_daily_delta_cap_from_event_meta():
    tension = RelationalTension()
    result = tension.apply_event(
        event_type="terse_streak", channel="qq", base_delta=20,
        reason="短回复", dedupe_key="terse:restore:1", occurred_at=at(0),
    )
    restored = RelationalTension()
    restored.restore(
        tension.snapshot(),
        [result.event.to_meta()],
        now=at(1),
    )

    second = restored.apply_event(
        event_type="terse_streak", channel="qq", base_delta=10,
        reason="短回复", dedupe_key="terse:restore:2", occurred_at=at(1),
    )

    assert second.applied is True
    assert restored.state.value == 20


def test_explicit_pause_is_qq_proactive_state_not_negative_event():
    state = RelationalTensionState()
    tension = RelationalTension(state)

    tension.set_explicit_pause(True, reason="用户要求不要主动找我")

    assert state.explicit_pause is True
    assert state.proactive_suppressed is True
    assert state.value == 0
    assert tension.proactive_allowed() is False

    tension.set_explicit_pause(False, reason="用户恢复主动")
    assert tension.proactive_allowed() is True


def test_prompt_hint_is_channel_and_expression_mode_aware():
    tension = RelationalTension()
    tension.state.value = 45
    tension.state.band = "cool"

    im = tension.prompt_hint(channel="im", expression_mode="direct")
    tts = tension.prompt_hint(channel="tts", expression_mode="restrained")

    assert "直接" in im
    assert "文字" in im
    assert "语音" in tts
    assert "克制" in tts


def test_high_tension_only_proposes_relationship_event_until_confirmed():
    tension = RelationalTension()
    tension.state.value = 70
    tension.state.band = "repair"
    tension.state.open_event_ids = ["tension-e1"]
    tension.state.last_cause = "QQ 主动问题没有得到回复"

    candidate = tension.relationship_event_candidate()

    assert candidate["kind"] == "relationship_event"
    assert candidate["needs_confirmation"] is True


def test_disabled_tension_does_not_change_prompt_or_create_candidate():
    tension = RelationalTension(config={"enabled": False})
    tension.state.value = 80
    tension.state.band = "high"
    tension.state.open_event_ids = ["tension-e1"]

    assert tension.prompt_hint() == "关系张力机制关闭，按角色原有状态自然回应。"
    assert tension.relationship_event_candidate() is None

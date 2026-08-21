from __future__ import annotations

import datetime

from veranima.core.qq_proactive import (
    QQProactiveEngine,
    QQProactiveState,
    QQUserState,
)


def test_time_factor_has_s_curve_bands():
    engine = QQProactiveEngine()

    values = [engine.time_factor(hours) for hours in (1, 4, 12, 48, 96)]

    assert values[0] < values[1] < values[2] < values[3]
    assert values[4] < values[3]
    assert 0.05 <= values[0] <= 0.10
    assert 0.55 <= values[4] <= 0.70


def test_readiness_uses_five_dimensions():
    engine = QQProactiveEngine()
    state = QQProactiveState(last_user_message_at="2026-08-21T08:00:00+08:00")
    now = datetime.datetime(2026, 8, 21, 20, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    result = engine.evaluate(
        state,
        now=now,
        momentum=1.2,
        routine_multiplier=0.3,
        material_multiplier=2.0,
        social_multiplier=1.3,
    )

    assert result.time_factor >= 0.5
    assert result.momentum == 1.2
    assert result.routine_multiplier == 0.3
    assert result.material_multiplier == 2.0
    assert result.social_multiplier == 1.3
    assert 0.0 <= result.score <= 1.0


def test_sleep_state_blocks_until_minimum_and_buffer():
    engine = QQProactiveEngine({"sleep_silence_hours": 8, "sleep_min_hours": 6, "post_silence_buffer_minutes": 30})
    state = QQProactiveState(
        user_state=QQUserState.SLEEPING,
        user_state_started_at="2026-08-21T00:00:00+08:00",
    )
    base = datetime.datetime(2026, 8, 21, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert engine.state_allows_proactive(state, base + datetime.timedelta(hours=5, minutes=59)) is False
    assert engine.state_allows_proactive(state, base + datetime.timedelta(hours=6)) is False
    assert engine.state_allows_proactive(state, base + datetime.timedelta(hours=8, minutes=29)) is False
    assert engine.state_allows_proactive(state, base + datetime.timedelta(hours=8, minutes=30)) is True


def test_user_message_clears_sleep_state():
    engine = QQProactiveEngine()
    state = QQProactiveState(
        user_state=QQUserState.SLEEPING,
        user_state_started_at="2026-08-21T00:00:00+08:00",
    )

    engine.note_user_message(state, "睡不着，我起来了")

    assert state.user_state == QQUserState.NORMAL
    assert state.user_state_started_at is None


def test_explicit_do_not_disturb_pauses_qq_only():
    engine = QQProactiveEngine()
    state = QQProactiveState()

    engine.note_user_message(state, "以后别主动找我")

    assert state.proactive_paused is True
    assert engine.state_allows_proactive(state, datetime.datetime.now().astimezone()) is False


def test_virtual_state_prefix_depends_on_qq_silence():
    engine = QQProactiveEngine()

    assert engine.virtual_state_prefix(1) == ""
    assert engine.virtual_state_prefix(4)
    assert engine.virtual_state_prefix(8)
    assert engine.virtual_state_prefix(24)


def test_state_signal_priority():
    engine = QQProactiveEngine()

    assert engine.detect_state("晚安，我今天心情很差") == QQUserState.SLEEPING
    assert engine.detect_state("我去开会，别找我") == QQUserState.BUSY
    assert engine.detect_state("让我静静") == QQUserState.LOW_MOOD
    assert engine.detect_state("拜拜") == QQUserState.CLOSING
    assert engine.detect_state("随便聊聊") == QQUserState.NORMAL


def test_threshold_schedule():
    engine = QQProactiveEngine()

    assert engine.schedule(0.2).action == "reevaluate"
    assert engine.schedule(0.2).delay_minutes == 60
    assert engine.schedule(0.5).delay_minutes == 15
    assert engine.schedule(0.7).action == "generate"
    assert engine.schedule(0.7).delay_minutes == 0

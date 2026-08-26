from __future__ import annotations

import json
import datetime as dt

import pytest

from veranima.core.virtual_schedule import (
    ScheduleOutline,
    ScheduleTemplateError,
    ScheduleRuntime,
)


def template(**overrides):
    value = {
        "enabled": True,
        "schema_version": 1,
        "timezone": "Asia/Shanghai",
        "default_day_profile": "baseline",
        "day_profiles": {"baseline": {"allowed_block_ids": ["focus"]}},
        "blocks": [{
            "id": "focus",
            "category": "role_defined",
            "activity_pool": ["focus_variant"],
            "preferred_window": {"start": "09:00", "end": "11:00"},
            "duration_minutes": {"min": 30, "max": 90},
            "required": True,
            "share_policy": "low_pressure",
            "interaction_profile": "occupied_brief",
            "interaction_impact": "inconvenient",
            "deviation_policy": {"allow_skip": False, "allow_shift": True},
        }],
        "circadian": {
            "wake_window": {"start": "07:00", "end": "09:00"},
            "sleep_window": {"start": "22:00", "end": "00:00"},
            "chronotype": "day_aligned",
            "recovery_rate_minutes_per_day": 20,
            "target_sleep_minutes": 480,
        },
        "interaction_profiles": {
            "occupied_brief": {"max_sentences": 2, "question_budget": 0}
        },
        "autonomy": {"max_deviations_per_day": 1},
    }
    value.update(overrides)
    return value


def test_loads_schedule_template_from_role_directory(tmp_path):
    role_dir = tmp_path / "characters" / "test-role"
    role_dir.mkdir(parents=True)
    path = role_dir / "virtual_schedule.json"
    path.write_text(json.dumps(template()), encoding="utf-8")

    outline = ScheduleOutline.from_role_dir(role_dir)

    assert outline.enabled is True
    assert outline.role_id == "test-role"
    assert outline.timezone == "Asia/Shanghai"
    assert outline.circadian.chronotype == "day_aligned"
    assert outline.blocks[0].id == "focus"


def test_missing_schedule_template_is_explicitly_disabled(tmp_path):
    role_dir = tmp_path / "characters" / "empty-role"
    role_dir.mkdir(parents=True)

    outline = ScheduleOutline.from_role_dir(role_dir)

    assert outline.enabled is False
    assert outline.blocks == ()


def test_invalid_schedule_template_fails_closed(tmp_path):
    role_dir = tmp_path / "characters" / "bad-role"
    role_dir.mkdir(parents=True)
    value = template()
    value["blocks"][0]["interaction_impact"] = "invented"
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ScheduleTemplateError, match="interaction_impact"):
        ScheduleOutline.from_role_dir(role_dir)


def test_schedule_template_rejects_block_not_allowed_by_profile(tmp_path):
    role_dir = tmp_path / "characters" / "bad-profile"
    role_dir.mkdir(parents=True)
    value = template()
    value["day_profiles"]["baseline"]["allowed_block_ids"] = ["missing"]
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ScheduleTemplateError, match="allowed_block_ids"):
        ScheduleOutline.from_role_dir(role_dir)


def test_day_plan_is_stable_and_current_context_uses_timezone(tmp_path):
    role_dir = tmp_path / "characters" / "stable-role"
    role_dir.mkdir(parents=True)
    (role_dir / "virtual_schedule.json").write_text(json.dumps(template()), encoding="utf-8")
    outline = ScheduleOutline.from_role_dir(role_dir)

    first = outline.build_day_plan(dt.datetime(2026, 8, 28, 10, tzinfo=dt.timezone.utc))
    second = outline.build_day_plan(dt.datetime(2026, 8, 28, 10, tzinfo=dt.timezone.utc))

    assert first.plan_id == second.plan_id
    assert first.items == second.items
    current = first.context_at(dt.datetime(2026, 8, 28, 1, 10, tzinfo=dt.timezone.utc))
    assert current.item_id == first.items[0].id
    assert current.interaction_profile == "occupied_brief"
    assert current.reply_budget["max_sentences"] == 2
    assert current.curiosity_allowed is False
    assert current.source_anchor["truth_class"] == "virtual_simulation"


def test_disabled_outline_has_no_plan_or_context(tmp_path):
    outline = ScheduleOutline.from_role_dir(tmp_path / "characters" / "missing")

    assert outline.build_day_plan(dt.datetime.now(dt.timezone.utc)) is None


def test_structured_plan_adjustment_rejects_template_external_activity(tmp_path):
    role_dir = tmp_path / "characters" / "bounded"
    role_dir.mkdir(parents=True)
    (role_dir / "virtual_schedule.json").write_text(json.dumps(template()), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))
    baseline = runtime.generate_next_day(
        dt.datetime(2026, 8, 28, 22, tzinfo=dt.timezone.utc),
        {"items": [{"rule_id": "outside", "activity_key": "invented"}]},
    )

    assert baseline.source == "deterministic_fallback"
    assert all(item.rule_id == "focus" for item in baseline.items)


def test_sleep_runtime_stops_at_grace_limit_and_records_sleep_debt(tmp_path):
    role_dir = tmp_path / "characters" / "sleepy"
    role_dir.mkdir(parents=True)
    value = template()
    value["sleep"] = {"grace_period_minutes": 30, "max_extension_minutes": 30}
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))
    start = dt.datetime(2026, 8, 28, 22, 0, tzinfo=dt.timezone.utc)

    assert runtime.begin_sleep_preparation(start).state == "sleep_preparing"
    extended = runtime.extend_wakefulness(start + dt.timedelta(minutes=20))
    assert extended.grace_deadline == start + dt.timedelta(minutes=50)
    final = runtime.extend_wakefulness(start + dt.timedelta(minutes=61))

    assert final.state == "sleeping"
    assert final.sleep_debt_minutes > 0
    assert runtime.extend_wakefulness(start + dt.timedelta(minutes=40)).state == "sleeping"


def test_sleep_transition_generates_next_day_once_with_structured_llm(tmp_path):
    role_dir = tmp_path / "characters" / "planner"
    role_dir.mkdir(parents=True)
    (role_dir / "virtual_schedule.json").write_text(json.dumps(template()), encoding="utf-8")
    calls = []

    def planner(_prompt):
        calls.append(1)
        return {"day_profile": "baseline", "items": [{
            "rule_id": "focus", "activity_key": "focus_variant", "operation": "none",
        }]}

    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir), planner=planner)
    when = dt.datetime(2026, 8, 28, 22, 0, tzinfo=dt.timezone.utc)
    runtime.begin_sleep_preparation(when)
    runtime.extend_wakefulness(when + dt.timedelta(minutes=31))
    first = runtime.generate_next_day_after_sleep(when)
    second = runtime.generate_next_day_after_sleep(when)

    assert first.plan_id == second.plan_id
    assert first.source == "llm_structured_template"
    assert calls == [1]


def test_runtime_advance_enters_sleep_from_scheduled_sleep_item(tmp_path):
    role_dir = tmp_path / "characters" / "advance"
    role_dir.mkdir(parents=True)
    value = template()
    value["blocks"] = [{
        "id": "sleep", "category": "sleep_window", "activity_pool": ["sleep"],
        "preferred_window": {"start": "22:00", "end": "23:00"},
        "duration_minutes": {"min": 30, "max": 60}, "required": True,
        "share_policy": "never", "interaction_profile": "occupied_brief",
        "interaction_impact": "unavailable", "deviation_policy": {"allow_shift": True},
    }]
    value["day_profiles"]["baseline"]["allowed_block_ids"] = ["sleep"]
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))

    state = runtime.advance(dt.datetime(2026, 8, 28, 14, 0, tzinfo=dt.timezone.utc))

    assert state.state == "sleep_preparing"
    assert runtime.current_context(dt.datetime(2026, 8, 28, 14, 0, tzinfo=dt.timezone.utc)).interaction_profile == "occupied_brief"


def test_background_advance_does_not_extend_sleep_repeatedly(tmp_path):
    role_dir = tmp_path / "characters" / "advance-sleep"
    role_dir.mkdir(parents=True)
    value = template()
    value["blocks"] = [{
        "id": "sleep", "category": "sleep_window", "activity_pool": ["sleep"],
        "preferred_window": {"start": "22:00", "end": "23:00"}, "duration_minutes": {"min": 30, "max": 60},
        "required": True, "share_policy": "never", "interaction_profile": "occupied_brief",
        "interaction_impact": "unavailable", "deviation_policy": {},
    }]
    value["day_profiles"]["baseline"]["allowed_block_ids"] = ["sleep"]
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))
    start = dt.datetime(2026, 8, 28, 14, tzinfo=dt.timezone.utc)
    runtime.advance(start)
    deadline = runtime.state.grace_deadline

    runtime.advance(deadline + dt.timedelta(minutes=1))
    after = runtime.state.grace_deadline

    assert runtime.sleeping
    assert after == deadline


def test_runtime_wakes_and_rotates_to_next_cycle(tmp_path):
    role_dir = tmp_path / "characters" / "wake-cycle"
    role_dir.mkdir(parents=True)
    value = template()
    value["blocks"] = [{
        "id": "sleep", "category": "sleep_window", "activity_pool": ["sleep"],
        "preferred_window": {"start": "22:00", "end": "23:00"}, "duration_minutes": {"min": 30, "max": 60},
        "required": True, "share_policy": "never", "interaction_profile": "occupied_brief",
        "interaction_impact": "unavailable", "deviation_policy": {},
    }]
    value["day_profiles"]["baseline"]["allowed_block_ids"] = ["sleep"]
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir), planner=lambda _: None)
    start = dt.datetime(2026, 8, 28, 14, tzinfo=dt.timezone.utc)
    runtime.advance(start)
    runtime.advance(runtime.state.grace_deadline + dt.timedelta(minutes=1))
    old_cycle = runtime.state.sleep_cycle_id

    state = runtime.advance(start + dt.timedelta(hours=9))

    assert state.state == "awake"
    assert state.sleep_cycle_id != old_cycle
    assert runtime.current_context(start + dt.timedelta(hours=9)).phase != "sleep_like"


def test_empty_planner_output_is_labeled_deterministic_fallback(tmp_path):
    role_dir = tmp_path / "characters" / "empty-planner"
    role_dir.mkdir(parents=True)
    (role_dir / "virtual_schedule.json").write_text(json.dumps(template()), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir), planner=lambda _: None)

    plan = runtime.generate_next_day(dt.datetime(2026, 8, 28, 22, tzinfo=dt.timezone.utc), None)

    assert plan.source == "deterministic_fallback"

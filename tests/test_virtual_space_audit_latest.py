from __future__ import annotations

import datetime as dt
import json

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime


def role(tmp_path):
    root = tmp_path / "characters" / "offline"
    root.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {},
        "space": {"world_scope": {"id": "scope", "home_place_id": "home"}, "places": [{"id": "home", "label": "家", "kind": "home", "sleep_allowed": True}, {"id": "work", "label": "工作区", "kind": "workspace", "sleep_allowed": False}], "routes": [{"from_place_id": "home", "to_place_id": "work", "duration_minutes": 10}]},
    }
    (root / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    return root


def test_unknown_after_downtime_is_not_promoted_to_arrival_by_advance(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.transition_started_at = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    runtime.expected_arrival_at = runtime.transition_started_at + dt.timedelta(minutes=10)
    runtime.reconcile_after_downtime(dt.datetime(2026, 8, 28, 2, tzinfo=dt.timezone.utc))

    runtime.advance(dt.datetime(2026, 8, 28, 3, tzinfo=dt.timezone.utc))

    assert runtime.scene_state == "unknown_after_downtime"
    assert runtime.current_place_id == "home"
    assert runtime.target_place_id == "work"


def test_explicit_reconcile_can_complete_unknown_transition(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "unknown_after_downtime"
    runtime.expected_arrival_at = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)

    runtime.reconcile_after_downtime(dt.datetime(2026, 8, 28, 2, tzinfo=dt.timezone.utc), arrived=True)

    assert runtime.scene_state == "at_place"
    assert runtime.current_place_id == "work"
    assert runtime.target_place_id is None


def test_unknown_scene_state_is_exposed_to_schedule_context(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "unknown_after_downtime"
    context = runtime.current_context(dt.datetime(2026, 8, 28, 3, tzinfo=dt.timezone.utc))
    assert context.scene_state == "unknown_after_downtime"


def test_current_scene_exposes_previous_and_timing_fields(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.transition_started_at = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    runtime.expected_arrival_at = dt.datetime(2026, 8, 28, 1, 30, tzinfo=dt.timezone.utc)
    scene = runtime.current_scene(dt.datetime(2026, 8, 28, 1, 10, tzinfo=dt.timezone.utc))
    assert scene["previous_place_id"] == "home"
    assert scene["transition_started_at"]
    assert scene["expected_arrival_at"]
    assert scene["confidence"] == "planned_current"

from __future__ import annotations

import datetime as dt
import json

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime


def make_role(tmp_path):
    root = tmp_path / "characters" / "route-p0"
    root.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai", "default_day_profile": "base",
        "day_profiles": {"base": {"allowed_block_ids": ["a", "b"]}},
        "blocks": [
            {"id": "a", "category": "obligation", "activity_pool": ["a"], "preferred_window": {"start": "09:00", "end": "10:00"}, "duration_minutes": {"min": 30, "max": 30}, "required": True, "share_policy": "never", "interaction_profile": "normal", "interaction_impact": "none", "deviation_policy": {}, "place_requirement": {"place_policy": "fixed", "fixed_place_id": "home"}},
            {"id": "b", "category": "obligation", "activity_pool": ["b"], "preferred_window": {"start": "10:30", "end": "12:00"}, "duration_minutes": {"min": 30, "max": 30}, "required": True, "share_policy": "never", "interaction_profile": "normal", "interaction_impact": "none", "deviation_policy": {}, "place_requirement": {"place_policy": "fixed", "fixed_place_id": "work"}},
        ],
        "interaction_profiles": {"normal": {}}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {},
        "space": {"world_scope": {"id": "s", "home_place_id": "home"}, "places": [{"id": "home", "label": "家", "kind": "home", "sleep_allowed": True}, {"id": "work", "label": "工作区", "kind": "workspace", "sleep_allowed": False}], "routes": [{"from_place_id": "home", "to_place_id": "work", "duration_minutes": 10, "bidirectional": True}]},
    }
    (root / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    return root


def test_runtime_transition_arrives_and_is_not_overwritten_by_old_context(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    before = dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc)
    at_change = dt.datetime(2026, 8, 28, 2, 30, tzinfo=dt.timezone.utc)
    after = dt.datetime(2026, 8, 28, 2, 41, tzinfo=dt.timezone.utc)
    runtime.advance(before)
    runtime.advance(at_change)
    assert runtime.current_context(at_change).scene_state == "in_transition"
    runtime.advance(after)
    context = runtime.current_context(after)
    assert context.scene_state == "at_place"
    assert context.place_id == "work"
    assert runtime.current_place_id == "work"


def test_runtime_snapshot_numeric_corruption_fails_closed(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    restored = ScheduleRuntime.from_snapshot(runtime.outline, {"role_id": "route-p0", "schedule_offset_minutes": "bad", "sleep_debt_minutes": "bad"})
    assert restored.schedule_offset_minutes == 0
    assert restored.state.sleep_debt_minutes == 0

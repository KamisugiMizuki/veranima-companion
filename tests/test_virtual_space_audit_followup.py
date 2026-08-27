from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime

from test_virtual_space_p0 import make_role


def test_unknown_scene_can_be_explicitly_reconciled_from_user_confirmation(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "unknown_after_downtime"
    runtime.expected_arrival_at = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    when = dt.datetime(2026, 8, 28, 2, tzinfo=dt.timezone.utc)
    assert runtime.reconcile_from_user("我到了", when) is True
    assert runtime.current_place_id == "work"
    assert runtime.scene_state == "at_place"
    assert runtime.pending_scene_event == "place_reconciled"


def test_gap_scene_keeps_known_place_state(tmp_path):
    from test_virtual_space_p0 import make_role
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.scene_state = "at_place"
    context = runtime.current_context(dt.datetime(2026, 8, 28, 3, tzinfo=dt.timezone.utc))
    assert context.scene_state == "at_place"
    assert context.place_id == "home"


def test_reconciling_does_not_claim_arrival(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "reconciling"
    assert runtime.current_scene(dt.datetime(2026, 8, 28, 3, tzinfo=dt.timezone.utc))["scene_state"] == "reconciling"
    assert runtime.reconcile_after_downtime(dt.datetime(2026, 8, 28, 3, tzinfo=dt.timezone.utc))["scene_state"] == "reconciling"


def test_restart_reconciliation_converges_at_matching_active_place(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    when = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    runtime.current_place_id = "home"
    runtime.scene_state = "at_place"

    runtime.reconcile_after_downtime(when)
    runtime.advance(when)

    assert runtime.scene_state == "at_place"
    assert runtime.pending_scene_event == "place_reconciled"


def test_pending_scene_event_survives_snapshot(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.pending_scene_event = "transition_completed"

    restored = ScheduleRuntime.from_snapshot(runtime.outline, runtime.to_snapshot())

    assert restored.pending_scene_event == "transition_completed"


def _choice_runtime(tmp_path):
    role = make_role(tmp_path)
    path = role / "virtual_schedule.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["blocks"][0]["place_requirement"] = {
        "place_policy": "choose", "preferred_place_ids": ["home", "work"],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    runtime.space_preference = "balanced"
    return runtime


def test_next_day_generation_uses_space_preference(tmp_path):
    runtime = _choice_runtime(tmp_path)

    plan = runtime.generate_next_day(dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc))

    assert plan.items[0].place_id == "work"


def test_next_day_restore_uses_space_preference(tmp_path):
    runtime = _choice_runtime(tmp_path)
    when = dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc)
    runtime._next_day_plan = runtime.outline.build_day_plan(when, space_preference="balanced")

    restored = ScheduleRuntime.from_snapshot(runtime.outline, runtime.to_snapshot())

    assert restored._next_day_plan.items[0].place_id == "work"

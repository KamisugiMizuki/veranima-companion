from __future__ import annotations

import datetime as dt
import json

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime


def test_space_preference_changes_choose_selection(tmp_path):
    role = tmp_path / "characters" / "choice"
    role.mkdir(parents=True)
    value = {"enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai", "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": ["rest"]}}, "blocks": [{"id": "rest", "category": "rest", "activity_pool": ["quiet"], "preferred_window": {"start": "10:00", "end": "12:00"}, "duration_minutes": {"min": 30, "max": 30}, "required": False, "share_policy": "normal", "interaction_profile": "normal", "interaction_impact": "none", "deviation_policy": {}, "place_requirement": {"place_policy": "choose", "preferred_place_ids": ["quiet", "home"], "allowed_place_tags": ["rest"]}}], "interaction_profiles": {"normal": {}}, "autonomy": {}, "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {}, "space": {"world_scope": {"id": "s", "home_place_id": "home"}, "places": [{"id": "home", "label": "家", "kind": "home", "tags": ["rest"], "sleep_allowed": True}, {"id": "quiet", "label": "安静处", "kind": "public_quiet", "tags": ["rest"], "sleep_allowed": False}], "routes": []}}
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    outline = ScheduleOutline.from_role_dir(role)
    assert outline.build_day_plan(dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc), space_preference="stable").items[0].place_id == "quiet"
    assert outline.build_day_plan(dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc), space_preference="balanced").items[0].place_id in {"home", "quiet"}


def test_explicit_reconcile_event_is_recordable(tmp_path):
    # Runtime must expose an event kind suitable for the shared virtual-life store.
    assert hasattr(ScheduleRuntime, "scene_event")

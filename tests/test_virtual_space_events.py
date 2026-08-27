from __future__ import annotations

import datetime as dt
import json
import pytest

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime, ScheduleTemplateError
from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class Embed:
    dim = 8
    def embed(self, texts): return [[0.0] * 8 for _ in texts]


class LLM:
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs): return '{"segments":[{"text":"收到"}]}'


def space_template_for_restart():
    return {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base",
        "day_profiles": {"base": {"allowed_block_ids": []}, "rest_like": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {},
        "space": {"world_scope": {"id": "scope", "home_place_id": "home"}, "places": [{"id": "home", "label": "家", "kind": "home", "sleep_allowed": True}], "routes": []},
    }


def test_space_event_is_persisted_with_virtual_truth_class(tmp_path):
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    event_id = memory.store_virtual_life_event(
        role_id="zima", event_kind="place_entered", plan_id="p1", item_id="i1",
        summary="进入工作区域", source={"place_id": "workspace", "truth_class": "virtual_simulation"},
    )
    event = memory.virtual_life_events("zima")[0]
    assert event["id"] == event_id
    assert event["event_kind"] == "place_entered"
    assert event["truth_class"] == "virtual_simulation"
    assert event["source"]["place_id"] == "workspace"


def test_runtime_snapshot_restores_scene_transition(tmp_path):
    # 由 runtime 已建立的移动状态应能在重启后保留出发地、目标地和到达时间。
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {},
    }
    role = tmp_path / "characters" / "scene"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    outline = ScheduleOutline.from_role_dir(role)
    runtime = ScheduleRuntime(outline)
    runtime.current_place_id = "home"
    runtime.target_place_id = "workspace"
    runtime.transition_started_at = dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc)
    runtime.expected_arrival_at = runtime.transition_started_at + dt.timedelta(minutes=10)
    restored = ScheduleRuntime.from_snapshot(outline, runtime.to_snapshot())
    assert restored.current_place_id == "home"
    assert restored.target_place_id == "workspace"


def test_downtime_recovery_marks_scene_unknown_without_fabricating_arrival(tmp_path):
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {},
        "space": {"world_scope": {"id": "scope", "home_place_id": "home"}, "places": [{"id": "home", "label": "家", "kind": "home", "sleep_allowed": True}, {"id": "work", "label": "工作区", "kind": "workspace", "sleep_allowed": False}], "routes": [{"from_place_id": "home", "to_place_id": "work", "duration_minutes": 10}]},
    }
    role = tmp_path / "characters" / "downtime"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.transition_started_at = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    runtime.expected_arrival_at = runtime.transition_started_at + dt.timedelta(minutes=10)

    scene = runtime.reconcile_after_downtime(dt.datetime(2026, 8, 28, 2, tzinfo=dt.timezone.utc))

    assert scene["scene_state"] == "unknown_after_downtime"
    assert scene["current_place_id"] == "home"
    assert scene["target_place_id"] == "work"


def test_current_space_answer_uses_scene_label_without_internal_ids(tmp_path):
    role = tmp_path / "characters" / "answer"
    role.mkdir(parents=True)
    (role / "character.json").write_text("{}", encoding="utf-8")
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai", "default_day_profile": "base",
        "day_profiles": {"base": {"allowed_block_ids": []}}, "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480}, "sleep": {},
        "space": {"world_scope": {"id": "scope", "home_place_id": "home"}, "places": [{"id": "home", "label": "窗边", "kind": "home", "sleep_allowed": True}], "routes": []},
    }
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Answer"), memory, LLM(), AgentState(), config={"root": str(tmp_path), "character_card": str(role / "character.json")})
    agent.schedule_runtime.current_place_id = "home"
    agent.schedule_runtime.scene_state = "at_place"
    answer = agent.current_space_answer()
    assert "窗边" in answer
    assert "home" not in answer


def test_space_overrides_survive_agent_restart(tmp_path):
    role = tmp_path / "characters" / "override"
    role.mkdir(parents=True)
    (role / "character.json").write_text("{}", encoding="utf-8")
    value = space_template_for_restart()
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    cfg = {"root": str(tmp_path), "character_card": str(role / "character.json"), "virtual_schedule": {"enabled": True, "space_enabled": False, "day_profile": "rest_like"}}
    first = Agent(CharacterCard(name="Override"), memory, LLM(), AgentState(), config=cfg)
    first._persist_state()
    second = Agent(CharacterCard(name="Override"), memory, LLM(), None, config=cfg)
    assert second.schedule_runtime.space_enabled is False
    assert second.schedule_runtime.profile_override == "rest_like"


def test_corrupt_scene_timestamps_restore_as_unknown_not_crash(tmp_path):
    role = tmp_path / "characters" / "corrupt"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(json.dumps(space_template_for_restart()), encoding="utf-8")
    outline = ScheduleOutline.from_role_dir(role)
    restored = ScheduleRuntime.from_snapshot(outline, {
        "role_id": "corrupt", "state": "awake", "transition_started_at": "not-a-time",
        "expected_arrival_at": "also-not-a-time", "sleep_started_at": None,
    })
    assert restored.scene_state == "unknown"
    assert restored.transition_started_at is None
    assert restored.expected_arrival_at is None


def test_sleep_activity_requires_sleep_allowed_place(tmp_path):
    value = space_template_for_restart()
    value["blocks"].append({"id": "sleep", "category": "sleep_window", "activity_pool": ["sleep"], "preferred_window": {"start": "22:00", "end": "23:00"}, "duration_minutes": {"min": 30, "max": 60}, "required": True, "share_policy": "never", "interaction_profile": "occupied_brief", "interaction_impact": "unavailable", "deviation_policy": {}, "place_requirement": {"place_policy": "fixed", "fixed_place_id": "home"}})
    value["day_profiles"]["base"]["allowed_block_ids"].append("sleep")
    value["space"]["places"][0]["sleep_allowed"] = False
    role = tmp_path / "characters" / "sleep-invalid"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ScheduleTemplateError):
        ScheduleOutline.from_role_dir(role).build_day_plan(dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc))

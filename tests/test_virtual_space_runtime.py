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


class ProbeLLM:
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs): return '{"segments":[{"text":"收到"}]}'


def space_template():
    return {
        "enabled": True,
        "schema_version": 1,
        "timezone": "Asia/Shanghai",
        "default_day_profile": "base",
        "day_profiles": {"base": {"allowed_block_ids": ["work", "rest"]}},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {},
        "blocks": [
            {"id": "work", "category": "obligation", "activity_pool": ["code"], "preferred_window": {"start": "09:00", "end": "11:00"}, "duration_minutes": {"min": 30, "max": 60}, "required": True, "share_policy": "never", "interaction_profile": "occupied_brief", "interaction_impact": "inconvenient", "deviation_policy": {}, "place_requirement": {"place_policy": "fixed", "fixed_place_id": "desk"}},
            {"id": "rest", "category": "rest", "activity_pool": ["quiet"], "preferred_window": {"start": "12:00", "end": "14:00"}, "duration_minutes": {"min": 30, "max": 60}, "required": False, "share_policy": "low_pressure", "interaction_profile": "rest_low_pressure", "interaction_impact": "none", "deviation_policy": {}, "place_requirement": {"place_policy": "fixed", "fixed_place_id": "window"}},
        ],
        "interaction_profiles": {"occupied_brief": {}, "rest_low_pressure": {}},
        "autonomy": {},
        "space": {
            "world_scope": {"id": "town", "kind": "fictional_town", "home_place_id": "desk"},
            "places": [
                {"id": "desk", "label": "工作区域", "kind": "workspace", "tags": ["focus"], "interaction_impact": "inconvenient", "sleep_allowed": False, "ambient_profile": {"light": "screen_cool", "sound": "quiet_keyboard"}},
                {"id": "window", "label": "窗边", "kind": "home", "tags": ["rest"], "interaction_impact": "none", "sleep_allowed": True, "ambient_profile": {"light": "soft_daylight", "sound": "outside_quiet"}},
            ],
            "routes": [{"from_place_id": "desk", "to_place_id": "window", "duration_minutes": 5, "mode": "indoor_transition", "bidirectional": True}],
        },
    }


def write_role(tmp_path):
    role = tmp_path / "characters" / "space-role"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(json.dumps(space_template()), encoding="utf-8")
    return role


def test_activity_change_updates_current_scene_and_ambient_context(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(write_role(tmp_path)))
    plan = runtime.outline.build_day_plan(dt.datetime(2026, 8, 28, 10, tzinfo=dt.timezone.utc))

    runtime.advance(dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc))
    work = runtime.current_context(dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc))
    runtime.advance(dt.datetime(2026, 8, 28, 4, 0, tzinfo=dt.timezone.utc))
    moving = runtime.current_context(dt.datetime(2026, 8, 28, 4, 0, tzinfo=dt.timezone.utc))
    runtime.advance(dt.datetime(2026, 8, 28, 4, 6, tzinfo=dt.timezone.utc))
    rest = runtime.current_context(dt.datetime(2026, 8, 28, 4, 6, tzinfo=dt.timezone.utc))

    assert work.place_id == "desk"
    assert work.ambient_context["light"] == "screen_cool"
    assert moving.scene_state == "in_transition"
    assert moving.ambient_context["state"] == "moving"
    assert rest.place_id == "window"
    assert rest.ambient_context["light"] == "soft_daylight"
    assert work.place_id != rest.place_id


def test_llm_cannot_select_unknown_place(tmp_path):
    outline = ScheduleOutline.from_role_dir(write_role(tmp_path))
    runtime = ScheduleRuntime(outline)
    with pytest.raises(ScheduleTemplateError):
        outline.build_day_plan(
            dt.datetime(2026, 8, 28, 10, tzinfo=dt.timezone.utc),
            adjustments=[{"rule_id": "work", "operation": "none", "place_id": "unknown"}],
        )


def test_old_role_without_space_keeps_schedule_context_compatible(tmp_path):
    role = write_role(tmp_path)
    value = json.loads((role / "virtual_schedule.json").read_text(encoding="utf-8"))
    value.pop("space")
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    outline = ScheduleOutline.from_role_dir(role)
    plan = outline.build_day_plan(dt.datetime(2026, 8, 28, 10, tzinfo=dt.timezone.utc))
    context = plan.context_at(dt.datetime(2026, 8, 28, 1, 30, tzinfo=dt.timezone.utc))
    assert context.place_id is None
    assert context.ambient_context == {}


def test_place_change_enters_transition_before_arrival(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(write_role(tmp_path)))
    work_time = dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc)
    rest_time = dt.datetime(2026, 8, 28, 4, 0, tzinfo=dt.timezone.utc)
    runtime.advance(work_time)
    runtime.advance(rest_time)
    moving = runtime.current_context(rest_time)
    arrived = runtime.current_context(rest_time + dt.timedelta(minutes=6))

    assert moving.scene_state == "in_transition"
    assert moving.place_id == "desk"
    assert moving.target_place_id == "window"
    assert arrived.scene_state == "at_place"
    assert arrived.place_id == "window"


def test_two_consecutive_place_changes_can_start_new_transition(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(write_role(tmp_path)))
    first = dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc)
    second = dt.datetime(2026, 8, 28, 4, 0, tzinfo=dt.timezone.utc)
    third = dt.datetime(2026, 8, 29, 1, 0, tzinfo=dt.timezone.utc)
    runtime.advance(first)
    runtime.advance(second)
    runtime.advance(second + dt.timedelta(minutes=6))
    runtime.advance(third)

    context = runtime.current_context(third)

    assert context.scene_state == "in_transition"
    assert context.place_id == "window"
    assert context.target_place_id == "desk"


def test_space_setting_can_disable_environment_without_disabling_schedule(tmp_path):
    role = write_role(tmp_path)
    card_path = role / "character.json"
    card_path.write_text("{}", encoding="utf-8")
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="SpaceOff"), memory, ProbeLLM(), AgentState(), config={
        "root": str(tmp_path), "character_card": str(card_path),
        "virtual_schedule": {"enabled": True, "space_enabled": False},
    })
    context = agent.schedule_runtime.current_context(dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc))

    assert context.activity_category == "obligation"
    assert context.place_id is None
    assert context.ambient_context == {}


def test_day_route_inserts_transition_and_consumes_time(tmp_path):
    outline = ScheduleOutline.from_role_dir(write_role(tmp_path))
    route = outline.build_day_route(dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc))

    assert route.stops[0].place_id == "desk"
    assert route.transitions[0].from_place_id == "desk"
    assert route.transitions[0].to_place_id == "window"
    assert route.transitions[0].planned_end > route.transitions[0].planned_start
    assert route.transitions[0].planned_end <= route.stops[1].planned_start


@pytest.mark.parametrize("role_id", ["zima", "yuki"])
def test_production_role_day_route_is_complete(role_id):
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    outline = ScheduleOutline.from_role_dir(root / "characters" / role_id)
    route = outline.build_day_route(dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc))
    assert route.stops


def test_missing_route_does_not_teleport(tmp_path):
    role = write_role(tmp_path)
    value = json.loads((role / "virtual_schedule.json").read_text(encoding="utf-8"))
    value["space"]["routes"] = []
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    runtime.advance(dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc))
    runtime.advance(dt.datetime(2026, 8, 28, 4, tzinfo=dt.timezone.utc))
    assert runtime.current_place_id == "desk"
    assert runtime.scene_state == "reconciling"

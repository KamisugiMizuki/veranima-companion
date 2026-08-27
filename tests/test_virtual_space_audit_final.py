from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore

from test_virtual_space_p0 import make_role


class Embed:
    dim = 8
    def embed(self, texts): return [[0.0] * 8 for _ in texts]


class LLM:
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs): return '{"segments":[{"text":"收到"}]}'


def test_space_disabled_still_allows_sleep_lifecycle(tmp_path):
    role = make_role(tmp_path)
    outline = ScheduleOutline.from_role_dir(role)
    runtime = ScheduleRuntime(outline)
    runtime.space_enabled = False
    now = dt.datetime(2026, 8, 28, 14, tzinfo=dt.timezone.utc)

    runtime.advance(now)
    runtime.advance(now + dt.timedelta(minutes=31))

    assert runtime.state.state == "sleeping"


def test_snapshot_invalid_ids_and_states_fail_closed(tmp_path):
    outline = ScheduleOutline.from_role_dir(make_role(tmp_path))
    runtime = ScheduleRuntime.from_snapshot(outline, {
        "role_id": "route-p0", "state": "not-a-state", "scene_state": "not-a-scene",
        "current_place_id": {"bad": True}, "target_place_id": ["bad"],
    })

    assert runtime.state.state == "awake"
    assert runtime.scene_state == "unknown"
    assert runtime.current_place_id is None
    assert runtime.target_place_id is None


def test_broken_transition_timestamp_degrades_to_unknown(tmp_path):
    outline = ScheduleOutline.from_role_dir(make_role(tmp_path))
    runtime = ScheduleRuntime(outline)
    runtime.scene_state = "in_transition"
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.expected_arrival_at = None

    scene = runtime.current_scene(dt.datetime(2026, 8, 28, 3, tzinfo=dt.timezone.utc))

    assert scene["scene_state"] == "unknown_after_downtime"


def test_runtime_route_respects_profile_allowlist(tmp_path):
    role = make_role(tmp_path)
    value = json.loads((role / "virtual_schedule.json").read_text(encoding="utf-8"))
    value["space"]["routes"][0]["allowed_day_profiles"] = ["other"]
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))

    runtime.advance(dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc))
    runtime.advance(dt.datetime(2026, 8, 28, 2, 30, tzinfo=dt.timezone.utc))

    assert runtime.scene_state == "reconciling"
    assert runtime.target_place_id is None


def test_profile_override_uses_one_plan_for_context_and_route(tmp_path):
    role = make_role(tmp_path)
    path = role / "virtual_schedule.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["day_profiles"]["other"] = {"allowed_block_ids": ["a", "b"]}
    value["space"]["routes"][0]["allowed_day_profiles"] = ["other"]
    path.write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    runtime.profile_override = "other"
    when = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)

    runtime.advance(when)

    assert runtime.day_route.plan_id == runtime.current_context(when).plan_id

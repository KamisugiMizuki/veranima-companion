from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from veranima.adapters.qq import QQAdapter
from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
from veranima.memory.store import MemoryStore
from veranima.pet_server import PetServer

from test_virtual_space_events import Embed, LLM
from test_virtual_space_p0 import make_role


def _agent_for_role(tmp_path, role):
    card_path = role / "character.json"
    card_path.write_text("{}", encoding="utf-8")
    return Agent(
        CharacterCard(name="Audit"),
        MemoryStore(str(tmp_path / "audit.sqlite"), config={}, provider=Embed()),
        LLM(),
        AgentState(),
        config={"root": str(tmp_path), "character_card": str(card_path)},
    )


def test_reconciling_transition_expires_to_unknown_instead_of_arriving(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "reconciling"
    runtime.transition_started_at = dt.datetime(2026, 8, 27, 15, 40, tzinfo=dt.timezone.utc)
    runtime.expected_arrival_at = dt.datetime(2026, 8, 27, 15, 50, tzinfo=dt.timezone.utc)

    runtime.advance(dt.datetime(2026, 8, 27, 16, 5, tzinfo=dt.timezone.utc))

    assert runtime.current_place_id == "home"
    assert runtime.target_place_id == "work"
    assert runtime.scene_state == "unknown_after_downtime"
    assert runtime.pending_scene_event == "place_unknown_after_downtime"


def test_disabled_space_does_not_complete_stale_transition(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.space_enabled = False
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "in_transition"
    runtime.expected_arrival_at = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)

    runtime.advance(dt.datetime(2026, 8, 28, 2, tzinfo=dt.timezone.utc))

    assert runtime.pending_scene_event == ""
    assert runtime.current_place_id is None


def test_event_dedup_keeps_distinct_kind_for_same_scene(tmp_path):
    agent = _agent_for_role(tmp_path, make_role(tmp_path))
    runtime = agent.schedule_runtime
    when = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    runtime.advance(when)
    runtime.scene_state = "at_place"
    scene = runtime.current_scene(when)
    runtime.last_scene_event_key = json.dumps(scene, ensure_ascii=False, sort_keys=True)
    runtime.pending_scene_event = "place_reconciled"

    asyncio.run(agent.advance_schedule_async(when))

    events = agent.memory.virtual_life_events(runtime.outline.role_id)
    assert [event["event_kind"] for event in events] == ["place_reconciled"]


def test_active_next_day_plan_drives_route_profile(tmp_path):
    role = make_role(tmp_path)
    path = role / "virtual_schedule.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["day_profiles"]["other"] = {"allowed_block_ids": ["a", "b"]}
    value["space"]["routes"][0]["allowed_day_profiles"] = ["other"]
    path.write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    first = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    change = dt.datetime(2026, 8, 28, 2, 30, tzinfo=dt.timezone.utc)
    runtime._next_day_plan = runtime.outline.build_day_plan(first, day_profile="other")
    runtime._next_day_profile = "other"

    runtime.advance(first)
    runtime.advance(change)

    assert runtime.day_route.plan_id == runtime._next_day_plan.plan_id
    assert runtime.day_route.transitions
    assert runtime.scene_state == "in_transition"


def test_reconciling_has_non_downtime_event_kind(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.scene_state = "reconciling"

    event = runtime.scene_event(dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc))

    assert event["event_kind"] == "transition_interrupted"


def test_reconciling_target_accepts_user_arrival_confirmation(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    runtime.current_place_id = "home"
    runtime.target_place_id = "work"
    runtime.scene_state = "reconciling"
    when = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)

    assert runtime.reconcile_from_user("我到了", when) is True
    assert runtime.current_place_id == "work"
    assert runtime.scene_state == "at_place"


def test_agent_path_persists_transition_completion(tmp_path):
    agent = _agent_for_role(tmp_path, make_role(tmp_path))
    before = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
    change = dt.datetime(2026, 8, 28, 2, 30, tzinfo=dt.timezone.utc)
    arrived = dt.datetime(2026, 8, 28, 2, 41, tzinfo=dt.timezone.utc)

    asyncio.run(agent.advance_schedule_async(before))
    asyncio.run(agent.advance_schedule_async(change))
    asyncio.run(agent.advance_schedule_async(arrived))

    kinds = [event["event_kind"] for event in agent.memory.virtual_life_events(agent.schedule_runtime.outline.role_id)]
    assert "transition_completed" in kinds


class _RuntimeProbe:
    sleeping = False

    def __init__(self):
        self.direct_calls = 0

    def advance(self, when):
        self.direct_calls += 1

    def to_snapshot(self):
        return {"direct_calls": self.direct_calls}

    def pop_notice(self):
        return ""


class _AgentProbe:
    def __init__(self):
        self.config = {}
        self.memory = SimpleNamespace()
        self.schedule_runtime = _RuntimeProbe()
        self.async_calls = 0
        self.active_calls = 0
        self.max_active_calls = 0

    def _persist_state(self):
        pass

    async def advance_schedule_async(self):
        self.async_calls += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(0)
        self.active_calls -= 1


def test_standalone_qq_background_uses_event_persistence_path(monkeypatch):
    agent = _AgentProbe()
    adapter = QQAdapter(agent, allowed_qq=[1], quiet_hours=None)
    adapter.bot = SimpleNamespace(_loop=object())
    adapter._in_quiet_hours = lambda: True

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _seconds):
            self.stopped = True

    class ImmediateFuture:
        def __init__(self, coroutine):
            self.coroutine = coroutine

        def result(self, timeout=None):
            return asyncio.run(self.coroutine)

    monkeypatch.setattr(
        asyncio,
        "run_coroutine_threadsafe",
        lambda coroutine, _loop: ImmediateFuture(coroutine),
    )

    adapter._bg_loop(Stop())

    assert agent.async_calls == 1
    assert agent.schedule_runtime.direct_calls == 0


def test_pet_and_qq_schedule_ticks_share_agent_lock():
    agent = _AgentProbe()

    async def run_both():
        lock = asyncio.Lock()
        adapter = QQAdapter(agent, allowed_qq=[1], quiet_hours=None, agent_lock=lock)
        server = PetServer(port=0)
        server._agent = agent
        server._agent_lock = lock
        await asyncio.gather(adapter._advance_schedule_once(), server._schedule_tick_once())

    asyncio.run(run_both())

    assert agent.max_active_calls == 1

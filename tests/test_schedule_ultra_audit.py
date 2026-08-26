from __future__ import annotations

import datetime as dt
import json

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
from veranima.memory.store import MemoryStore


class Embed:
    dim = 8
    def embed(self, texts): return [[0.0] * 8 for _ in texts]


class LLM:
    def __init__(self, fail=False): self.fail = fail
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs):
        if self.fail: return ""
        return '{"segments":[{"text":"醒了，也看到你之前发的消息了。"}]}'


def make_role(tmp_path, role="audit"):
    root = tmp_path / "characters" / role
    root.mkdir(parents=True)
    (root / "character.json").write_text("{}", encoding="utf-8")
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": ["focus"]}},
        "blocks": [{"id": "focus", "category": "obligation", "activity_pool": ["read"], "preferred_window": {"start": "08:00", "end": "12:00"}, "duration_minutes": {"min": 30, "max": 60}, "required": True, "share_policy": "normal", "interaction_profile": "normal", "interaction_impact": "none", "deviation_policy": {}}],
        "interaction_profiles": {"normal": {}}, "autonomy": {},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {"grace_period_minutes": 0, "max_extension_minutes": 0},
    }
    (root / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    return root


def test_day_close_is_unique_per_role_and_sleep_cycle(tmp_path):
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    first = memory.store_virtual_life_event(role_id="audit", event_kind="day_close_summary", summary="first", source={"sleep_cycle_id": "cycle-1"})
    second = memory.store_virtual_life_event(role_id="audit", event_kind="day_close_summary", summary="second", source={"sleep_cycle_id": "cycle-1"})
    assert first == second
    assert len(memory.virtual_life_events("audit")) == 1


def test_wake_keeps_generated_plan_and_original_sleep_cycle(tmp_path):
    outline = ScheduleOutline.from_role_dir(make_role(tmp_path))
    runtime = ScheduleRuntime(outline, planner=lambda _: {"day_profile": "base", "items": [{"rule_id": "focus", "activity_key": "read", "operation": "shift", "shift_minutes": 15, "duration_minutes": 30}]})
    start = dt.datetime(2026, 8, 28, 14, tzinfo=dt.timezone.utc)
    runtime.begin_sleep_preparation(start)
    runtime.extend_wakefulness(start)
    runtime.generate_next_day_after_sleep(start)
    plan_id = runtime._next_day_plan.plan_id
    cycle = runtime.state.sleep_cycle_id
    runtime.advance(start + dt.timedelta(minutes=481))
    assert runtime._next_day_plan is not None
    assert runtime._next_day_plan.plan_id == plan_id
    assert runtime.last_sleep_cycle_id == cycle


def test_sleep_archive_processed_only_after_successful_reply(tmp_path):
    role = make_role(tmp_path)
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Audit"), memory, LLM(fail=True), AgentState(), config={"root": str(tmp_path), "character_card": str(role / "character.json")})
    runtime = agent.schedule_runtime
    cycle = "audit:2026-08-28"
    mid = memory.store_message("user", "睡觉后发的消息", channel="qq")
    memory.archive_sleep_message(role_id="audit", user_scope="qq:default", sleep_cycle_id=cycle, message_id=mid, sender_scope="qq:default")
    runtime.last_sleep_cycle_id = cycle
    runtime.state = runtime.state.__class__(state="awake", sleep_cycle_id="audit:2026-08-29:awake", sleep_reason="woke")
    agent.handle("早上好", channel="im")
    rows = memory.sleep_messages("audit", "qq:default", cycle)
    assert rows[0]["processed_at"] is None


def test_finished_activity_is_not_recomputed(tmp_path):
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(make_role(tmp_path)))
    start = dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc)
    runtime.start_activity("item", start)
    first = runtime.finish_activity(start + dt.timedelta(minutes=30))
    second = runtime.finish_activity(start + dt.timedelta(hours=9))
    assert second == first


def test_self_share_and_curiosity_settings_are_respected(tmp_path):
    role = make_role(tmp_path)
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Audit"), memory, LLM(), AgentState(), config={
        "root": str(tmp_path), "character_card": str(role / "character.json"),
        "virtual_schedule": {"enabled": True, "self_share": "off", "curiosity": "off"},
    })
    memory.store_virtual_life_event(role_id="audit", event_kind="day_close_summary", summary="x", source={"sleep_cycle_id": "c"})
    mid = memory.store_message("user", "我喜欢推理", channel="qq")
    memory.upsert_user_info_gap(role_id="audit", user_scope="qq:42", topic_key="推理", reason="why", source_message_id=mid)
    assert agent.schedule_self_share_candidate("qq") is None
    assert agent.schedule_curiosity_candidate("qq", user_scope="qq:42") is None


def test_curiosity_candidate_carries_owner_scope(tmp_path):
    role = make_role(tmp_path)
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Audit"), memory, LLM(), AgentState(), config={
        "root": str(tmp_path), "character_card": str(role / "character.json"),
        "virtual_schedule": {"enabled": True, "curiosity": "low"},
    })
    mid = memory.store_message("user", "我喜欢推理", channel="qq")
    memory.upsert_user_info_gap(role_id="audit", user_scope="qq:42", topic_key="推理", reason="why", source_message_id=mid)
    candidate = agent.schedule_curiosity_candidate("qq", user_scope="qq:42")
    assert candidate.context["owner_scope"] == "qq:42"

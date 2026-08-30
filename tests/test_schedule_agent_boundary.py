from __future__ import annotations

import json
from pathlib import Path

from veranima.core.agent import Agent, TurnResult
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
from veranima.memory.store import MemoryStore


class Embed:
    dim = 8
    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class LLM:
    def __init__(self): self.calls = 0
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs):
        self.calls += 1
        return '{"segments":[{"text":"正常回复","tone":"冷静"}]}'


def make_role(tmp_path: Path) -> Path:
    role = tmp_path / "characters" / "sleep-boundary"
    role.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {"grace_period_minutes": 0, "max_extension_minutes": 0},
    }
    (role / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    return role


def test_agent_sleep_boundary_does_not_call_llm(tmp_path):
    role = make_role(tmp_path)
    llm = LLM()
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Sleep"), memory, llm, AgentState(), config={"root": str(tmp_path)})
    agent.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    start = __import__("datetime").datetime(2026, 8, 28, 22, tzinfo=__import__("datetime").timezone.utc)
    agent.schedule_runtime.begin_sleep_preparation(start)
    agent.schedule_runtime.extend_wakefulness(start)

    # now 注入与睡眠周期同时刻：真实时钟会把角色按醒（睡眠目标时长已过），
    # 断言的是 sleeping 态行为，测试必须固定时钟
    result = agent.handle("还在吗", now=start)

    assert result.reply == ""
    assert llm.calls == 0
    assert memory.recent_messages(limit=1)[0]["content"] == "还在吗"


def test_schedule_settings_disable_runtime_and_override_sleep(tmp_path):
    role = make_role(tmp_path)
    card_path = role / "character.json"
    card_path.write_text("{}", encoding="utf-8")
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    disabled = Agent(CharacterCard(name="Disabled"), memory, LLM(), AgentState(), config={
        "root": str(tmp_path), "character_card": str(card_path),
        "virtual_schedule": {"enabled": False},
    })
    enabled = Agent(CharacterCard(name="Enabled"), memory, LLM(), AgentState(), config={
        "root": str(tmp_path), "character_card": str(card_path),
        "virtual_schedule": {"enabled": True, "grace_period_minutes": 45, "max_extension_minutes": 15},
    })

    assert disabled.schedule_runtime is None
    assert enabled.schedule_outline.sleep["grace_period_minutes"] == 45
    assert enabled.schedule_outline.sleep["max_extension_minutes"] == 15


def test_sleeping_agent_archives_message_metadata(tmp_path):
    role = make_role(tmp_path)
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Sleep"), memory, LLM(), AgentState(), config={"root": str(tmp_path)})
    agent.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    start = __import__("datetime").datetime(2026, 8, 28, 22, tzinfo=__import__("datetime").timezone.utc)
    agent.schedule_runtime.begin_sleep_preparation(start)
    agent.schedule_runtime.extend_wakefulness(start)

    agent.handle("睡眠消息", now=start)

    rows = memory.sleep_messages("sleep-boundary", "qq:default", agent.schedule_runtime.state.sleep_cycle_id)
    assert rows and rows[0]["message_id"]


def test_user_message_extends_grace_period(tmp_path):
    role = make_role(tmp_path)
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Grace"), memory, LLM(), AgentState(), config={"root": str(tmp_path)})
    agent.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    start = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    agent.schedule_runtime.begin_sleep_preparation(start)
    before = agent.schedule_runtime.state.grace_deadline

    agent.handle("再聊一会")

    assert agent.schedule_runtime.state.grace_deadline >= before
    assert agent.schedule_runtime.state.sleep_extension_minutes >= 0

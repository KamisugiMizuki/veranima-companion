from __future__ import annotations

import datetime as dt
import json

from veranima.core.character import CharacterCard
from veranima.core.virtual_schedule import ScheduleOutline
from veranima.memory.store import MemoryStore
from veranima.core.agent import Agent
from veranima.core.state import AgentState


def space_template_for_prompt():
    return {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": ["focus"]}},
        "blocks": [{"id": "focus", "category": "obligation", "activity_pool": ["code"], "preferred_window": {"start": "09:00", "end": "11:00"}, "duration_minutes": {"min": 30, "max": 60}, "required": True, "share_policy": "never", "interaction_profile": "occupied_brief", "interaction_impact": "inconvenient", "deviation_policy": {}, "place_requirement": {"place_policy": "fixed", "fixed_place_id": "desk"}}],
        "interaction_profiles": {"occupied_brief": {"reply_style": "short_precise", "max_sentences": 2, "question_budget": 0}},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {}, "autonomy": {},
        "space": {"world_scope": {"id": "town", "kind": "fictional_town", "home_place_id": "desk"}, "places": [{"id": "desk", "label": "工作区域", "kind": "workspace", "allowed_day_profiles": ["base"], "allowed_activity_categories": ["obligation"], "ambient_profile": {"light": "屏幕冷光"}, "sleep_allowed": False}], "routes": []},
    }


class Embed:
    dim = 8
    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class ProbeLLM:
    def __init__(self):
        self.messages = None
    def is_model_loaded(self):
        return True
    def chat(self, messages, **kwargs):
        self.messages = messages
        return json.dumps({"segments": [{"text": "收到。", "tone": "冷静"}]}, ensure_ascii=False)


def test_agent_schedule_planner_consumes_raw_json_without_reply_parser(tmp_path):
    class PlannerLLM(ProbeLLM):
        def chat(self, messages, **kwargs):
            return json.dumps({"day_profile": "baseline", "items": [{
                "rule_id": "focus", "activity_key": "focus_variant", "operation": "none"
            }]}, ensure_ascii=False)

    agent = Agent(
        CharacterCard(name="Planner"),
        MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed()),
        PlannerLLM(), AgentState(), config={"root": str(tmp_path)},
    )

    result = agent._plan_schedule_with_llm(dt.datetime.now(dt.timezone.utc))

    assert result["items"][0]["rule_id"] == "focus"


def test_agent_prompt_consumes_current_schedule_context(tmp_path, monkeypatch):
    role_dir = tmp_path / "characters" / "probe"
    role_dir.mkdir(parents=True)
    template = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": ["focus"]}},
        "blocks": [{"id": "focus", "category": "obligation", "activity_pool": ["variant"],
                    "preferred_window": {"start": "09:00", "end": "11:00"},
                    "duration_minutes": {"min": 30, "max": 60}, "required": True,
                    "share_policy": "never", "interaction_profile": "occupied_brief",
                    "interaction_impact": "inconvenient", "deviation_policy": {}}],
        "interaction_profiles": {"occupied_brief": {"reply_style": "short_precise", "max_sentences": 2, "question_budget": 0}},
        "circadian": {"wake_window": {"start": "07:00", "end": "08:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "autonomy": {},
    }
    (role_dir / "virtual_schedule.json").write_text(json.dumps(template), encoding="utf-8")
    outline = ScheduleOutline.from_role_dir(role_dir)
    plan = outline.build_day_plan(dt.datetime(2026, 8, 28, 10, tzinfo=dt.timezone.utc))
    probe = ProbeLLM()
    memory = MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Probe"), memory, probe, AgentState(), config={"root": str(tmp_path), "chat": {"proactive_message_prob": 0.0}})
    monkeypatch.setattr(agent, "_schedule_context", lambda channel, now=None: plan.context_at(dt.datetime(2026, 8, 28, 1, 10, tzinfo=dt.timezone.utc)))

    result = agent.handle("随便聊聊")

    assert result.reply
    system = probe.messages[0]["content"]
    assert "occupied_brief" in system
    assert "max_sentences" in system or "最多" in system
    assert "plan_id" not in result.reply


def test_agent_prompt_consumes_current_place_and_ambient_context(tmp_path):
    role_dir = tmp_path / "characters" / "probe-space"
    role_dir.mkdir(parents=True)
    value = space_template_for_prompt()
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    card_path = role_dir / "character.json"
    card_path.write_text("{}", encoding="utf-8")
    agent = Agent(CharacterCard(name="Space"), MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed()), ProbeLLM(), AgentState(), config={"root": str(tmp_path), "character_card": str(card_path)})
    context = agent.schedule_runtime.current_context(dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc))

    text = agent._format_schedule_context(context)

    assert "工作区域" in text
    assert "屏幕冷光" in text


def test_agent_generates_schedule_notice_from_runtime_event(tmp_path):
    probe = ProbeLLM()
    agent = Agent(
        CharacterCard(name="Notice"),
        MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed()),
        probe, AgentState(), config={"root": str(tmp_path)},
    )

    assert agent.schedule_notice_text("unknown") == ""

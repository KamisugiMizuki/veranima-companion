from __future__ import annotations

import datetime as dt
import json

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.holiday_calendar import HolidayCalendar
from veranima.core.state import AgentState
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
from veranima.memory.store import MemoryStore


class Embed:
    dim = 8
    def embed(self, texts): return [[0.0] * 8 for _ in texts]


class LLM:
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs): return '{"segments":[{"text":"收到"}]}'


def test_calendar_fails_closed_to_weekend(monkeypatch):
    calendar = HolidayCalendar(timeout=1)
    monkeypatch.setattr(calendar, "_year", lambda year: {})
    assert calendar.day(dt.date(2026, 8, 29)).day_type == "rest_like"
    assert calendar.day(dt.date(2026, 8, 28)).day_type == "baseline"


def test_calendar_rejects_untrusted_base_url():
    import pytest
    with pytest.raises(ValueError):
        HolidayCalendar(base_url="https://invalid.invalid", timeout=1)


def test_virtual_life_archive_and_user_gap_produce_anchored_candidates(tmp_path):
    role = tmp_path / "characters" / "role"
    role.mkdir(parents=True)
    (role / "character.json").write_text("{}", encoding="utf-8")
    template = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {},
    }
    (role / "virtual_schedule.json").write_text(json.dumps(template), encoding="utf-8")
    memory = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Role"), memory, LLM(), AgentState(), config={
        "root": str(tmp_path), "character_card": str(role / "character.json")
    })
    memory.store_virtual_life_event(role_id="role", event_kind="day_close_summary", summary="今天完成了一些计划")
    message_id = memory.store_message("user", "我喜欢推理游戏", channel="qq")
    memory.upsert_user_info_gap(role_id="role", user_scope="qq:default", topic_key="推理游戏", reason="想了解原因", source_message_id=message_id)

    share = agent.schedule_self_share_candidate("qq")
    curiosity = agent.schedule_curiosity_candidate("qq")

    assert share.context["event_id"]
    assert curiosity.context["source_message_id"] == message_id

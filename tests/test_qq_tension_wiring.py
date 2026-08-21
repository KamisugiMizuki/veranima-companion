from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

from veranima.adapters.qq import QQAdapter
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class Embed:
    dim = 8

    def embed(self, texts):
        return [[0.1] * 8 for _ in texts]


class LLM:
    low_energy_max_tokens = 512

    def is_model_loaded(self):
        return True

    def chat(self, messages, **kwargs):
        return "知道了"


class Bot:
    async def send_private_msg(self, **kwargs):
        return {"ok": True}


def _adapter(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "qq-tension.db"), config={"embedding_model": "none"}, provider=Embed())
    from veranima.core.agent import Agent
    agent = Agent(
        CharacterCard(name="测试"), memory, LLM(), AgentState(),
        {"relationship_tension": {"high_tension_proactive": True}},
    )
    adapter = QQAdapter(agent, allowed_qq=["1"], quiet_hours=None)
    adapter.bot = Bot()
    return adapter


def test_successful_question_creates_pending_expectation(tmp_path):
    adapter = _adapter(tmp_path)
    candidate = adapter._qq_candidate_from_text("你后来怎么样？")

    adapter._record_qq_expectation(candidate, "你后来怎么样？")

    row = adapter.agent.memory.recent_proactive_feedback(channel="qq", limit=1)[0]
    assert row["requires_reply"] == 1
    assert row["expectation_status"] == "pending"


def test_expired_expectation_applies_tension_once(tmp_path):
    adapter = _adapter(tmp_path)
    old = "2026-08-21T00:00:00+00:00"
    adapter.agent.memory.record_proactive_feedback(
        source="scene", channel="qq", candidate_id="q1", sent_at=old,
        requires_reply=True, direct_question="你后来怎么样？",
        expires_at="2026-08-22T00:00:00+00:00",
    )

    now = dt.datetime(2026, 8, 23, tzinfo=dt.timezone.utc)
    adapter._expire_qq_expectations(now)
    first = adapter.agent.tension.state.value
    adapter._expire_qq_expectations(now)

    assert first == 10
    assert adapter.agent.tension.state.value == first
    assert adapter.agent.memory.recent_proactive_feedback(channel="qq", limit=1)[0]["expectation_status"] == "expired"


def test_qq_opportunity_send_records_reply_expectation(tmp_path):
    adapter = _adapter(tmp_path)
    candidate = adapter._qq_candidate_from_text("你后来怎么样？")
    adapter._qq_opportunity = {
        "candidate": candidate,
        "text": "你后来怎么样？",
        "created_at": 0,
    }

    asyncio.run(adapter._send_qq_opportunity_async())
    row = adapter.agent.memory.recent_proactive_feedback(channel="qq", limit=1)[0]
    assert row["requires_reply"] == 1
    assert row["expectation_status"] == "pending"


def test_high_tension_sends_one_repair_opportunity_per_event(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.agent.gate.quiet_hours_enabled = False
    adapter.agent.tension.state.value = 70
    adapter.agent.tension.state.band = "repair"
    adapter.agent.tension.state.open_event_ids = ["tension-event-1"]
    adapter.agent.tension.state.last_cause = "QQ 主动问题没有得到回复"
    adapter._generate_tension_repair = lambda event_id, now: "我有点在意刚才那件事，你只是忙了吗？"

    now = dt.datetime(2026, 8, 23, 12, tzinfo=dt.timezone.utc)
    assert asyncio.run(adapter._send_tension_repair_async(now)) is True
    assert asyncio.run(adapter._send_tension_repair_async(now)) is False

    rows = adapter.agent.memory.recent_proactive_feedback(channel="qq", limit=10)
    repairs = [row for row in rows if row["source"] == "relationship_repair"]
    assert len(repairs) == 1
    assert repairs[0]["requires_reply"] == 0


def test_open_qq_question_after_one_hour_creates_abandonment_event(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.agent.memory.store_message(
        "user", "我先说到这里", channel="qq",
    )
    adapter.agent.memory.store_message(
        "assistant", "你后来有没有试那个方案？", channel="qq",
    )

    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    adapter._check_qq_abandonment(now)

    assert adapter.agent.tension.state.value == 6.8
    assert adapter.agent.tension.state.open_event_ids


def test_abandonment_does_not_duplicate_pending_proactive_expectation(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.agent.memory.store_message("user", "先忙", channel="qq")
    assistant_id = adapter.agent.memory.store_message("assistant", "你后来怎么样？", channel="qq")
    adapter.agent.memory.record_proactive_feedback(
        source="scene", channel="qq", candidate_id="q1",
        requires_reply=True, direct_question="你后来怎么样？",
        expires_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=20)).isoformat(),
    )

    adapter._check_qq_abandonment(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))

    assert adapter.agent.tension.state.value == 0

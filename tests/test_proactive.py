"""主动触发测试：定时问候 / 节庆与纪念日检查（每日去重）。"""

from __future__ import annotations

import datetime

import pytest

from veranima.core.proactive import GreetingScheduler, MealReminderScheduler, OccasionChecker
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())


# ---------- 定时问候 ----------

def test_greeting_morning():
    g = GreetingScheduler()
    slot = g.due_greeting(now=datetime.datetime(2026, 8, 3, 8, 0))
    assert slot == "morning"
    assert "早" in g.greeting_text(slot)


def test_greeting_noon_evening():
    g = GreetingScheduler()
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 12, 0)) == "noon"
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 20, 0)) == "evening"


def test_greeting_off_window():
    g = GreetingScheduler()
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 3, 0)) is None  # 深夜不问候
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 15, 0)) is None  # 下午无问候


def test_greeting_daily_dedup():
    g = GreetingScheduler()
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 8, 0)) == "morning"
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 9, 0)) is None  # 同日同段不重复
    assert g.due_greeting(now=datetime.datetime(2026, 8, 4, 8, 0)) == "morning"  # 次日重新允许


# ---------- 三餐提醒 ----------

def test_meal_schedule_is_stable_and_within_ten_minutes():
    scheduler = MealReminderScheduler()
    day = datetime.date(2026, 8, 24)
    first = scheduler.scheduled_at(day, "breakfast")
    second = MealReminderScheduler().scheduled_at(day, "breakfast")
    assert first == second
    assert datetime.datetime(2026, 8, 24, 7, 50) <= first <= datetime.datetime(2026, 8, 24, 8, 10)


def test_meal_due_once_after_scheduled_time():
    scheduler = MealReminderScheduler()
    target = scheduler.scheduled_at(datetime.date(2026, 8, 24), "lunch")
    assert scheduler.due(now=target - datetime.timedelta(minutes=1), sent_ids=set()) is None
    due = scheduler.due(now=target, sent_ids=set())
    assert due and due[0] == "lunch" and "午饭" in due[1]
    assert scheduler.due(now=target, sent_ids={due[2]}) is None


def test_meal_does_not_fire_after_window():
    scheduler = MealReminderScheduler()
    assert scheduler.due(
        now=datetime.datetime(2026, 8, 24, 17, 11), sent_ids=set(),
    ) is None


def test_meal_due_accepts_timezone_aware_clock():
    scheduler = MealReminderScheduler()
    target = scheduler.scheduled_at(datetime.date(2026, 8, 24), "lunch")
    aware = target.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    assert scheduler.due(now=aware, sent_ids=set())


# ---------- 节庆与纪念日 ----------

def test_fixed_holiday():
    o = OccasionChecker()
    hit = o.due_occasion(memory=None, now=datetime.datetime(2026, 1, 1))
    assert hit and "元旦" in hit
    # 同日不重复
    assert o.due_occasion(memory=None, now=datetime.datetime(2026, 1, 1)) is None


def test_anniversary_from_memory(store):
    store.store("semantic", "我的生日是3月14日", importance=0.9, confidence=0.8)
    o = OccasionChecker()
    hit = o.due_occasion(store, now=datetime.datetime(2026, 3, 14))
    assert hit and "生日" in hit


def test_anniversary_no_date_no_hit(store):
    store.store("semantic", "我的生日快到了", importance=0.9, confidence=0.8)  # 无具体日期
    o = OccasionChecker()
    assert o.due_occasion(store, now=datetime.datetime(2026, 3, 14)) is None


def test_occasion_reaction_birthday():
    r = OccasionChecker.occasion_reaction("今天好像是你的特别日子：我的生日是3月14日")
    assert "生日" in r
    assert "谢谢你" in r


# ---------- Agent.tick_proactive（问候 + 节庆接线） ----------

class FakeLLM:
    def __init__(self, reply="早呀"):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply

    def is_model_loaded(self):
        return True

    low_energy_max_tokens = 256


@pytest.fixture
def agent(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState

    card = CharacterCard(name="小V", description="测试", personality="温柔")
    return Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=FakeLLM(),
        state=AgentState(),
        config={},
    )


def test_tick_proactive_morning_greeting(agent):
    agent.memory.store_message("user", "昨天面试通过了", 80, "开心")
    msgs = agent.tick_proactive(now=datetime.datetime(2026, 8, 3, 8, 0))
    assert len(msgs) == 1
    assert agent.llm.calls == 1  # 有最近上下文 → 个性化问候走 LLM
    # 已入档 memory（recent_messages 正序，最新在末尾）
    recent = agent.memory.recent_messages(limit=3)
    assert recent[-1]["role"] == "assistant"


def test_tick_proactive_can_defer_gate_commit(agent):
    agent.memory.store_message("user", "昨天面试通过了", 80, "开心")

    msgs = agent.tick_proactive(now=datetime.datetime(2026, 8, 3, 8, 0), commit=False)

    assert msgs
    assert agent.gate._today_count == {}


def test_tick_proactive_holiday(agent):
    """1月1日 15:00（非问候窗口）：只触发节日，不触发问候。"""
    msgs = agent.tick_proactive(now=datetime.datetime(2026, 1, 1, 15, 0))
    assert len(msgs) == 1
    assert "元旦" in msgs[0]


def test_tick_proactive_idempotent(agent):
    """同日同段不重复触发（每日去重）。"""
    now = datetime.datetime(2026, 8, 3, 8, 0)
    assert len(agent.tick_proactive(now=now)) == 1
    assert agent.tick_proactive(now=now) == []


def test_tick_proactive_no_trigger_off_window(agent):
    """非问候窗口 + 无节日：返回空。"""
    assert agent.tick_proactive(now=datetime.datetime(2026, 8, 3, 15, 0)) == []


# ---------- 状态：初始依恋度（DESIGN 6 章 2026-08） ----------

def test_state_initial_attachment_default():
    """默认初始依恋度 0.5：半亲密起步（落在亲密期门槛）。"""
    from veranima.core.state import AgentState
    s = AgentState()
    assert abs(s.attachment - 0.5) < 1e-9
    stage, _ = s.relationship_stage()
    assert stage == "亲密期"


def test_state_initial_attachment_custom():
    """显式指定 initial_attachment：生效且封顶 0.95。"""
    from veranima.core.state import AgentState
    s = AgentState(initial_attachment=0.8)
    assert abs(s.attachment - 0.8) < 1e-9
    s2 = AgentState(initial_attachment=1.5)
    assert abs(s2.attachment - 0.95) < 1e-9


def test_state_initial_attachment_explicit_zero_respected():
    """显式传 attachment=0.0 时尊重显式值（不使用默认 0.5）。"""
    from veranima.core.state import AgentState
    s = AgentState(attachment=0.0, initial_attachment=0.0)
    assert s.attachment == 0.0
    stage, _ = s.relationship_stage()
    assert stage == "初识期"

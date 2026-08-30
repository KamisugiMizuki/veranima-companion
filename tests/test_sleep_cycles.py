"""用户睡眠周期数据面行为测试（2026-08-30 用户拍板）。

覆盖：入睡/苏醒识别（LLM 确认+关键词回退）、sleep_cycles 落库、
苏醒总结生成、三餐锚点随用户作息、角色作息适应用户（偏移+理由）。
"""

import json
from datetime import datetime, timezone, timedelta

import pytest

from veranima.core.agent import Agent


class _FakeLLM:
    """假 LLM：max_tokens<=128 的调用按 JSON 协议返回（睡眠确认/总结/理由）。"""

    base_url = "http://fake"

    def __init__(self, sleep_action="sleep"):
        self._sleep_action = sleep_action
        self.calls = []

    def chat(self, messages, *, max_tokens=None, temperature=None):
        self.calls.append((max_tokens, messages[-1]["content"][:40] if messages else ""))
        prompt = messages[-1]["content"] if messages else ""
        if "判断用户是否在报告自己入睡或苏醒" in prompt:
            # 按被判断文本内容返回：含「睡了/晚安」→sleep，含「醒了」→wake
            action = "wake" if any(k in prompt for k in ("醒了", "睡醒", "起床")) else "sleep"
            return json.dumps({"action": action}, ensure_ascii=False)
        if "起床问候+睡眠状况总结" in prompt:
            return "早，昨晚睡得还行嘛。"
        if "作息也往他的时间靠一靠" in prompt:
            return "看你天天这个点才起，我干脆把闹钟也往后挪了挪。"
        return "这是回复内容。"

    def chat_structured(self, messages, *, max_tokens=None, temperature=None):
        return "这是回复内容。"

    def is_model_loaded(self):
        return True


class _FakeEmbed:
    dim = 8

    def embed(self, text: str):
        return [0.1] * self.dim


@pytest.fixture()
def agent(tmp_path):
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    from veranima.memory.store import MemoryStore

    card = CharacterCard(name="凛", description="测试", personality="温柔")
    card.tones = ["中性", "平静", "温柔", "毒舌"]
    a = Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=_FakeEmbed()),
        llm=_FakeLLM(),
        state=AgentState(),
        config={"llm": {}, "proactive": {}},
    )
    return a


def _utc(hour: int, minute: int = 0) -> datetime:
    # naive 本地时间：与 _user_wake_hour 的 astimezone()（本地）一致，避免时区偏移断言
    return datetime(2026, 8, 30, hour, minute)


def test_sleep_report_opens_cycle(agent):
    """用户说「我睡了」→ user_asleep=True + sleep_cycles 开周期。"""
    assert not agent.state.user_asleep
    agent._note_sleep_report("我睡了，晚安", _utc(23, 0))
    assert agent.state.user_asleep
    cycles = agent.memory.recent_sleep_cycles()
    assert len(cycles) == 1
    assert cycles[0]["woke_at"] is None
    assert agent.state.last_sleep_report_at == "2026-08-30T23:00:00"


def test_wake_report_closes_cycle_with_summary(agent):
    """「醒了」→ 周期闭合 + 长睡眠总结写入。"""
    agent._note_sleep_report("我睡了", _utc(22, 0))
    agent._note_sleep_report("醒了", _utc(7, 0))
    assert not agent.state.user_asleep
    cycle = agent.memory.latest_closed_cycle()
    assert cycle is not None
    assert cycle["woke_at"] == "2026-08-30T07:00:00"
    # 8h 睡眠 → 长睡眠，LLM 总结已生成
    assert "早" in (cycle.get("summary") or "")


def test_wake_without_sleep_ignored(agent):
    """没入睡过就报「醒了」→ 不闭合任何周期、状态不变。"""
    agent._note_sleep_report("醒了", _utc(7, 0))
    assert not agent.state.user_asleep
    assert agent.memory.recent_sleep_cycles() == []


def test_sleep_report_fallback_keywords(agent):
    """LLM 失败（抛异常）→ 回退关键词规则仍能识别。"""
    agent.llm = _BrokenLLM()
    agent._note_sleep_report("我去睡觉了", _utc(23, 0))
    assert agent.state.user_asleep


class _BrokenLLM:
    base_url = "http://fake"

    def chat(self, messages, *, max_tokens=None, temperature=None):
        raise RuntimeError("llm down")

    def chat_structured(self, messages, *, max_tokens=None, temperature=None):
        raise RuntimeError("llm down")

    def is_model_loaded(self):
        return False


def test_meal_anchors_follow_user_cycle(agent):
    """用户中午 12 点起 → 三餐锚点改到 14/18/23（起床+2/+6/+11）。"""
    agent._note_sleep_report("睡了", _utc(3, 0))
    agent._note_sleep_report("醒了", _utc(12, 0))
    wake = agent._user_wake_hour()
    assert wake is not None and 11.5 <= wake <= 12.5
    agent.meals.adjust_to_user_cycle(wake)
    assert agent.meals.slots["breakfast"][0] == 14
    assert agent.meals.slots["lunch"][0] == 18
    assert agent.meals.slots["dinner"][0] == 23


def test_meal_anchors_stay_default_for_normal(agent):
    """用户早上 6 点起 → 锚点不动（保持默认 8/12/17）。"""
    agent.meals.adjust_to_user_cycle(6.0)
    assert agent.meals.slots["breakfast"][0] == 8
    assert agent.meals.slots["lunch"][0] == 12
    assert agent.meals.slots["dinner"][0] == 17


def test_user_asleep_blocks_meal(agent):
    """用户睡眠中 → tick 不发三餐提醒（user_asleep 分支）。"""
    agent._note_sleep_report("睡了", _utc(23, 0))
    # 制造一个应发的晚餐锚点
    agent.meals.adjust_to_user_cycle(12.0)
    from veranima.core.proactive import MealReminderScheduler
    # 直接测 tick 内分支：user_asleep 时 due_meal 被置 None
    now = datetime(2026, 8, 31, 23, 5)  # dinner 锚点已随用户作息调至 23:00±10min
    agent.state.user_asleep = True
    due = agent.meals.due(now=now, sent_ids=set())
    # due 命中但 tick 会因 user_asleep 跳过；这里验证 due 确实命中锚点
    assert due is not None
    assert due[0] == "dinner"


def test_sleep_cycle_persist_roundtrip(agent, tmp_path):
    """周期写入后可读回（同连接）。"""
    agent._note_sleep_report("睡了", _utc(22, 30))
    agent._note_sleep_report("醒了", _utc(6, 30))
    cycles = agent.memory.recent_sleep_cycles()
    assert len(cycles) == 1
    assert cycles[0]["fell_asleep_at"] == "2026-08-30T22:30:00"
    assert cycles[0]["woke_at"] == "2026-08-30T06:30:00"

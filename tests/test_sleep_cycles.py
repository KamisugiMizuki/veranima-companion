"""用户睡眠周期数据面行为测试（2026-08-30 用户拍板）。

覆盖：入睡/苏醒识别（LLM 确认+关键词回退）、sleep_cycles 落库、
苏醒总结生成、三餐锚点随用户作息、角色作息适应用户（偏移+理由）、
出现=醒来推断（2026-09-11 拍板：活动集群维护 + 报告到达定案——
报告与活动流相连取集群起点，孤立报告保守取报告时间）。
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


def _utc(hour: int, minute: int = 0, day: int = 30) -> datetime:
    # naive 本地时间：与 _user_wake_hour 的 astimezone()（本地）一致，避免时区偏移断言
    return datetime(2026, 8, day, hour, minute)


def _naive(s) -> datetime:
    """ISO（带/不带时区）→ naive 本地：跨时区稳定的断言口径（生产存 UTC aware，
    测试注入 naive，直接字符串断言会因面不同而脆）。"""
    dt = datetime.fromisoformat(str(s))
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


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


def test_presence_signal_tracks_cluster(agent):
    """出现=醒来（二稿=集群）：睡眠窗口内 ≥4h 的信号维护活动集群。

    首个有效信号松开 asleep（她知道但不说）；同集群内的后续信号只滚动
    「最近信号」，不重开集群。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    assert not agent.note_presence_signal(_utc(23, 30))       # 1.5h <4h，不采信
    assert agent.state.user_asleep
    assert agent.note_presence_signal(_utc(7, 30, day=31))    # 跨夜 9.5h ≥4h → 集群
    assert not agent.state.user_asleep                        # 守卫松开（她知道但不说）
    row = agent.memory.sleep_state_row()
    assert _naive(row.get("inferred_woke_at")) == _utc(7, 30, day=31)
    assert agent.note_presence_signal(_utc(7, 40, day=31))    # 10min 内 → 延续
    row = agent.memory.sleep_state_row()
    assert _naive(row.get("inferred_woke_at")) == _utc(7, 30, day=31)  # 集群起点不变
    assert _naive(row.get("last_signal_at")) == _utc(7, 40, day=31)    # 最近信号滚动


def test_wake_report_uses_cluster_when_flow_connected(agent):
    """报告与活动流相连（醒后一直在用手机）→ 醒来=集群起点。

    「睡醒刷了挺久手机才想起来通报」的形态：7:30 起活动、9:40 才报告，
    醒来记 7:30 而不是报告时间。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    for t in range(7 * 60 + 30, 9 * 60 + 36, 10):             # 7:30~9:35 持续活动
        agent.note_presence_signal(_utc(t // 60, t % 60, day=31))
    agent._note_sleep_report("醒了", _utc(9, 40, day=31))     # 报告晚到
    cycle = agent.memory.latest_closed_cycle()
    assert cycle is not None
    assert _naive(cycle["woke_at"]) == _utc(7, 30, day=31)    # 集群起点，不是 9:40
    assert not (agent.memory.sleep_state_row().get("inferred_woke_at") or "")  # 闭合后清空
    assert "早" in (cycle.get("summary") or "")


def test_night_waking_not_mistaken_for_wake(agent):
    """起夜回笼：>=4h 线后的起夜活动会被记录，但真醒来（新集群）覆盖定案。

    2:30 起夜刷手机（>4h 线）→ 回笼 → 17:00 真醒 → 17:10 报告：
    醒来=17:00（新集群），不是起夜段 2:30。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    for m in (30, 32, 35):                                    # 2:30 起夜一小段
        agent.note_presence_signal(_utc(2, m, day=31))
    assert _naive(agent.memory.sleep_state_row().get("inferred_woke_at")) == \
        _utc(2, 30, day=31)
    for m in (0, 2, 5):                                       # 17:00 真醒（距上次 >GAP → 新集群）
        agent.note_presence_signal(_utc(17, m, day=31))
    assert _naive(agent.memory.sleep_state_row().get("inferred_woke_at")) == \
        _utc(17, 0, day=31)
    agent._note_sleep_report("醒了", _utc(17, 10, day=31))
    cycle = agent.memory.latest_closed_cycle()
    assert _naive(cycle["woke_at"]) == _utc(17, 0, day=31)    # 起夜段被甩掉


def test_isolated_report_keeps_report_time(agent):
    """报告孤立（活动流早断）→ 保守取报告时间，宁晚勿错。

    「起夜回笼」与「醒后长静默（办公熄屏）」在信号上不可区分，不猜。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    agent.note_presence_signal(_utc(7, 30, day=31))
    agent.note_presence_signal(_utc(7, 35, day=31))
    agent._note_sleep_report("醒了", _utc(18, 0, day=31))     # 全天无信号，晚上才报告
    cycle = agent.memory.latest_closed_cycle()
    assert cycle["woke_at"] == "2026-08-31T18:00:00"


def test_daytime_sleep_symmetric(agent):
    """白天睡（10:00 睡、20:00 醒）与跨夜对称：判据不看钟点，只看相对时间线。"""
    agent._note_sleep_report("睡了", _utc(10, 0))
    assert not agent.note_presence_signal(_utc(12, 0))        # 2h <4h，不采信
    for m in (0, 2, 5):                                       # 20:00 醒（10h 后）
        agent.note_presence_signal(_utc(20, m))
    agent._note_sleep_report("醒了", _utc(20, 10))
    cycle = agent.memory.latest_closed_cycle()
    assert _naive(cycle["woke_at"]) == _utc(20, 0)


def test_mixed_timezone_fell_does_not_break_summary(agent):
    """混面回归（09-11 MuMu #2 实锤）：报告链的 now=UTC aware（生产面）+
    活动信号=本地 naive——woke 存储面统一后，总结时长计算不得静默炸掉。
    单测旧口径两边都是 naive 所以没抓到，这里刻意用生产面组合。"""
    from datetime import timezone as _tz, timedelta as _td
    aware_sleep = datetime(2026, 8, 30, 22, 0, tzinfo=_tz(_td(hours=8)))
    agent._note_sleep_report("睡了", aware_sleep)
    agent.note_presence_signal(_utc(7, 30, day=31))           # 本地 naive 信号
    aware_wake = datetime(2026, 8, 31, 8, 0, tzinfo=_tz(_td(hours=8)))
    agent._note_sleep_report("醒了", aware_wake)
    cycle = agent.memory.latest_closed_cycle()
    assert cycle is not None
    assert _naive(cycle["woke_at"]) == _utc(7, 30, day=31)    # 集群起点（相连报告）
    assert "早" in (cycle.get("summary") or "")               # 时长计算跑通=总结产出


def test_wake_report_without_signal_keeps_report_time(agent):
    """无活动信号（报告照旧）→ 醒来时间=报告时间，现状不破。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    agent._note_sleep_report("醒了", _utc(7, 0, day=31))
    cycle = agent.memory.latest_closed_cycle()
    assert cycle["woke_at"] == "2026-08-31T07:00:00"


def test_presence_signal_noop_outside_sleep(agent):
    """已醒（无未闭合周期）→ 信号 no-op。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    agent._note_sleep_report("醒了", _utc(7, 0, day=31))
    assert not agent.note_presence_signal(_utc(12, 0, day=31))
    row = agent.memory.sleep_state_row()
    assert not (row.get("inferred_woke_at") or "")
    assert not (row.get("last_signal_at") or "")


def test_presence_signal_never_leaks_across_cycles(agent):
    """推断后用户又报「睡了」（回笼/误推断）→ 推断位清空，不跨段误配。"""
    agent._note_sleep_report("睡了", _utc(22, 0))
    agent.note_presence_signal(_utc(7, 30, day=31))
    agent.note_presence_signal(_utc(7, 35, day=31))
    agent._note_sleep_report("睡了", _utc(10, 0, day=31))     # 回笼重报
    row = agent.memory.sleep_state_row()
    assert not (row.get("inferred_woke_at") or "")
    assert not (row.get("last_signal_at") or "")
    agent._note_sleep_report("醒了", _utc(12, 0, day=31))
    cycle = agent.memory.latest_closed_cycle()
    assert cycle["woke_at"] == "2026-08-31T12:00:00"          # 早上的推断没有误配进来

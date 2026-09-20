"""USER_MOOD_SPEC P1/P2 行为验收（2026-09-20）。

P1：近况对照（确定性预筛）+ 判断点条件注入 + 极短消息放行 + 偏离级字段 + 留痕。
P2：回复软约束块 + 织文带状态 + 当日态（写入/读侧过期/角色边界）。
阈值口径来自 P0 离线校准（docs/mind/audits/USER_MOOD_P0_CALIBRATION.md）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.judges import MessageJudgment, _coerce, build_judge_prompt, judge_message
from veranima.core.mood import BASE_MIN_SAMPLES, compare, is_normal_message
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore

LONG = "今天下午开会开了两个小时，回来又改了一版方案，晚上再跑个回归就行"


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    base_url = "http://fake"

    def __init__(self, raw='{"emotion": "none"}'):
        self.raw = raw
        self.calls: list = []

    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return self.raw

    def chat_structured(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return self.raw

    def is_model_loaded(self):
        return True


def _rows(texts, *, minutes=5):
    base = dt.datetime(2026, 9, 20, 10, 0)
    return [{"role": "user", "content": t,
             "created_at": (base + dt.timedelta(minutes=minutes * i)).isoformat()}
            for i, t in enumerate(texts)]


def _store(tmp_path, name="t.db") -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / name), config={}, provider=FakeEmbed())


def _agent(tmp_path, llm, role="lin", name="t.db"):
    mem = _store(tmp_path, name)
    agent = Agent(card=CharacterCard(name="凛"), memory=mem, llm=llm, state=AgentState(), config={})
    agent.role_key = role
    return agent


# ---------- P1：对照层 ----------

def test_short_run_after_normal_baseline_hits():
    rows = _rows([LONG] * BASE_MIN_SAMPLES + ["嗯", "好", "算了", "随便"])
    cmp = compare(rows)
    assert cmp.hit is True
    assert cmp.run >= 4 and cmp.base >= 15
    block = cmp.context_block()
    assert "状态对照" in block and "user_mood" in block and "算了" in block


def test_short_baseline_never_hits():
    """他平时就只发几个字 → 长度没有分辨力，永不命中（宁可漏报）。"""
    rows = _rows(["在吗", "行", "好的", "哦"] * 8 + ["嗯", "哦", "嗯", "哦"])
    assert len(rows) >= BASE_MIN_SAMPLES
    assert compare(rows).hit is False


def test_adjacent_repeats_do_not_count_as_a_run():
    """同一条消息重发=重试/测试，不是「连着几句没力气」（P0 校准第一条）。"""
    rows = _rows([LONG] * BASE_MIN_SAMPLES + ["嗯", "嗯", "嗯", "嗯"])
    assert compare(rows).hit is False


def test_second_signal_alone_does_not_trigger():
    """只表情/标点异常、长度正常 → 不命中（第二信号不设独立门槛，Q1 ②）。"""
    rows = _rows([LONG + "！"] * BASE_MIN_SAMPLES
                 + ["今天过得还行没什么特别的", "就是有点累但还好", "晚上随便吃点就行"])
    assert compare(rows).hit is False


def test_cross_session_gap_breaks_the_run():
    """窗口只认同一会话：跨了 3 小时以上不算连发。"""
    rows = _rows([LONG] * BASE_MIN_SAMPLES, minutes=1)
    rows += _rows(["嗯", "好"], minutes=1)[:2]
    rows += [{"role": "user", "content": t, "created_at": (dt.datetime(2026, 9, 21, 9, 0)
             + dt.timedelta(minutes=i)).isoformat()} for i, t in enumerate(["嗯", "嗯"])]
    assert compare(rows).hit is False


# ---------- P1：判断点接线 ----------

def test_prompt_is_byte_identical_when_not_hit():
    """未命中：送判文本与改造前逐字一致（零噪声、零成本）。"""
    assert build_judge_prompt("今天想吃什么", "") == build_judge_prompt("今天想吃什么", "", None, "")


def test_short_message_released_when_comparison_hits():
    llm = FakeLLM('{"user_mood": "low"}')
    assert judge_message(llm, "嗯", "") is None                  # 预筛照旧挡住
    j = judge_message(llm, "嗯", "", allow_short=True)
    assert j is not None and j.user_mood == "low"
    assert len(llm.calls) == 1


def test_user_mood_is_closed_set():
    assert _coerce({"user_mood": "LOW"}).user_mood == "low"
    assert _coerce({"user_mood": "very_sad"}).user_mood == "none"
    assert MessageJudgment().user_mood == "none"


def test_mood_context_only_enters_when_hit():
    llm = FakeLLM("{}")
    judge_message(llm, "嗯", "", allow_short=True)
    assert "user_mood" not in json.dumps(llm.calls[0]["messages"], ensure_ascii=False)


# ---------- P1/P2：agent 端（留痕 + 软约束 + 当日态） ----------

def _seed_short_run(agent):
    for _ in range(BASE_MIN_SAMPLES):
        agent.memory.store_message("user", LONG, role_id=agent.role_key)
    for t in ("嗯", "好", "算了", "随便"):
        agent.memory.store_message("user", t, role_id=agent.role_key)


def test_hit_logs_decision_and_writes_day_state(tmp_path):
    agent = _agent(tmp_path, FakeLLM('{"emotion": "sad", "user_mood": "low"}'))
    _seed_short_run(agent)
    j = agent.turn_judgment("算了")
    assert j.user_mood == "low"

    rows = agent.memory.con.execute(
        "SELECT kind FROM decisions WHERE role_id=? ORDER BY id DESC LIMIT 10",
        (agent.role_key,)).fetchall()
    kinds = [r["kind"] for r in rows]
    assert "judge:user_mood" in kinds                       # 留痕（可回放）
    state = agent.memory.user_mood_state(role_id="lin")
    assert state["level"] == "low" and state["role"] == "lin"
    assert agent._user_mood_block()                         # 回复软约束在场
    assert "别追问" in agent._user_mood_block() and "不点破" not in agent._user_mood_block()


def test_day_state_is_role_scoped_and_expires(tmp_path):
    agent = _agent(tmp_path, FakeLLM())
    agent.memory.set_user_mood_state({"date": agent._dt_now().date().isoformat(),
                                      "level": "low", "role": "lin", "note": "x"})
    assert agent.memory.user_mood_state(role_id="lin")["level"] == "low"
    assert agent.memory.user_mood_state(role_id="xumian") == {}      # 别的角色不知情
    assert agent.memory.user_mood_state(role_id="lin", today="2020-01-01") == {}  # 读侧过期


def test_normal_message_clears_day_state(tmp_path):
    """同日纠正：他写长了、也没负面情绪 → 解除约束（§2.3.1）；短句不解除。"""
    agent = _agent(tmp_path, FakeLLM('{"emotion": "none", "user_mood": "none"}'))
    agent.memory.set_user_mood_state({"date": agent._dt_now().date().isoformat(),
                                      "level": "low", "role": "lin", "note": "x"})
    agent.turn_judgment("嗯")                                # 极短：不算恢复证据
    assert agent.memory.user_mood_state(role_id="lin")

    agent._judgment_for = ""                                # 下一轮换了句话
    agent.turn_judgment(LONG)
    assert agent.memory.user_mood_state(role_id="lin") == {}


def test_weave_note_only_when_low(tmp_path):
    agent = _agent(tmp_path, FakeLLM())
    assert agent._weave_mood_note() == ""
    agent.memory.set_user_mood_state({"date": agent._dt_now().date().isoformat(),
                                      "level": "low", "role": "lin", "note": "x"})
    note = agent._weave_mood_note()
    assert note and "背景" in note and "别追问" in note        # 不挡发，只收语气


def test_is_normal_message_gate():
    assert is_normal_message(LONG) and not is_normal_message("嗯")

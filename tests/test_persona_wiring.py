"""P-6/P-9 产品链路连通（PERSONA_LOOP_SPEC 15 唯一调用方向）：handle 内真实消费验证。"""
from __future__ import annotations

import pytest


def _agent(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore
    card = CharacterCard(name="测试卡", veranima={"inner_tensions": [{"left": "渴望靠近", "right": "害怕失去边界"}]})
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    captured = {}

    class ProbeLLM:
        def __init__(self):
            self.calls = 0
            self.last_prompt = ""
            self.last_messages = None

        def chat(self, messages, max_tokens=None):
            self.calls += 1
            self.last_messages = messages
            self.last_prompt = next((m["content"] for m in messages if m["role"] == "system"), "")
            return "嗯，我明白你的意思。"

        def is_model_loaded(self):
            return True

        low_energy_max_tokens = 512

    llm = ProbeLLM()
    a = Agent(card=card, memory=store, llm=llm, state=None, config={})
    a._probe_llm = llm
    return a, llm


def test_handle_injects_reuse_action_when_framework_relevant(tmp_path):
    a, llm = _agent(tmp_path)
    # 先建立框架记忆
    a.handle("我认为边界比热情可靠")
    # 相关话题 → 应注入【回用动作】与【理解用户】
    a.handle("你觉得边界和热情怎么权衡")
    assert "【回用动作】" in llm.last_prompt
    assert "边界比热情可靠" in llm.last_prompt


def test_handle_injects_expression_plan_for_conflict(tmp_path):
    a, llm = _agent(tmp_path)
    from veranima.core.persona import apply_relationship_event
    a.relationship = apply_relationship_event(a.relationship, {"type": "boundary_violation", "cause": "测试冲突", "event_id": "c1"})
    a.relationship.conflict_tension = 0.7  # 明确未消解压力（>0.5 触发计划）
    a.handle("你刚才为什么那样说")
    assert "【表达意图】" in llm.last_prompt


def test_handle_simple_chat_skips_plan(tmp_path):
    a, llm = _agent(tmp_path)
    a.handle("今天天气不错")
    assert "【表达意图】" not in llm.last_prompt


def test_handle_returns_style_hint(tmp_path):
    a, _ = _agent(tmp_path)
    r = a.handle("今天心情好，分享个好消息")
    assert r.style_hint in ("short", "normal", "long")


def test_handle_imprint_from_positive_feedback(tmp_path):
    a, _ = _agent(tmp_path)
    a.handle("你说得对，很有道理")  # POSITIVE_WORDS 命中 → depth 印记 candidate
    assert a._imprints.status("depth") in ("candidate", "active", "rejected")


def test_handle_imprint_blocked_by_correction(tmp_path):
    a, _ = _agent(tmp_path)
    a.handle("你说得对，很有道理")
    a.handle("不对，你理解错了")  # CORRECTION_WORDS 命中 → 拒绝方向
    assert a._imprints.status("depth") == "rejected"


def test_reuse_cooldown_limits_handle(tmp_path):
    a, llm = _agent(tmp_path)
    a.handle("我认为边界比热情可靠")
    # 连续相关话题：第一次注入动作，8 轮内第二次不再注入
    a.handle("边界怎么权衡")
    assert "【回用动作】" in llm.last_prompt
    a.handle("边界再讨论一下")
    assert "【回用动作】" not in llm.last_prompt

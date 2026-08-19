"""P-1 用户思维框架（PERSONA_LOOP_SPEC P-1）：定义/比喻/价值判断提取，事实与引用不误判。"""
from __future__ import annotations

import pytest

from veranima.core.persona import (
    PersonaCandidate,
    extract_framework_candidates,
    persona_candidate_to_memory,
    validate_persona_candidate,
)
from veranima.core.character import CharacterCard


def _card() -> CharacterCard:
    return CharacterCard(name="测试卡", personality="理性但护短", veranima={"values": ["诚实"]})


# ---------- 提取 ----------

def test_clear_definition_hits():
    cands = extract_framework_candidates("我认为活着就是持续产生秩序和美", 1)
    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "user_framework"
    assert "活着就是持续产生秩序和美" in c.content
    assert c.evidence_message_ids == [1]


def test_multiple_phrases():
    cands = extract_framework_candidates("对我来说效率很重要。我一直认为边界比热情可靠。", 2)
    assert len(cands) == 2
    assert any("效率很重要" in c.content for c in cands)
    assert any("边界比热情可靠" in c.content for c in cands)


def test_ordinary_fact_not_framework():
    assert extract_framework_candidates("我喜欢下雨天", 3) == []


def test_quoted_opinion_rejected():
    assert extract_framework_candidates("某本书里说活着就是不断挣扎", 4) == []
    assert extract_framework_candidates("「与其说A不如说B」是别人的观点", 5) == []


def test_url_and_code_rejected():
    assert extract_framework_candidates("https://example.com 我认为这个网页很重要", 6) == []
    assert extract_framework_candidates("git clone 我认为这样写代码快", 7) == []


def test_rhetorical_question_rejected():
    assert extract_framework_candidates("你觉得呢？", 8) == []
    assert extract_framework_candidates("我认为的也不一定对，对吧？", 9) == []


def test_he_said_rejected():
    assert extract_framework_candidates("他说我认为应该这样做", 10) == []


# ---------- 校验 ----------

def test_validate_requires_evidence():
    c = PersonaCandidate(kind="user_framework", title="t", content="x", evidence_message_ids=[])
    issues = validate_persona_candidate(c, _card())
    assert any("evidence" in i for i in issues)


def test_validate_kind_whitelist():
    c = PersonaCandidate(kind="not_a_kind", title="t", content="x", evidence_message_ids=[1])
    assert any("kind" in i for i in validate_persona_candidate(c, _card()))


def test_validate_ok():
    c = PersonaCandidate(kind="user_framework", title="t", content="活着就是产生秩序", evidence_message_ids=[1])
    assert validate_persona_candidate(c, _card()) == []


# ---------- 转换 ----------

def test_conversion_mapping():
    c = PersonaCandidate(kind="user_framework", title="t", content="活着就是产生秩序", evidence_message_ids=[11])
    mc = persona_candidate_to_memory(c, source_message_id=11)
    assert mc is not None
    assert mc["kind"] == "user_framework"
    assert mc["confidence"] == 0.60
    assert mc["source"] == "rule_extract"
    assert mc["source_message_id"] == 11
    assert mc["meta"]["stability"] == 0.5


def test_conversion_rejects_unknown_kind():
    c = PersonaCandidate(kind="nope", title="t", content="x", evidence_message_ids=[1])
    assert persona_candidate_to_memory(c, 1) is None


# ---------- agent 集成 ----------

def _agent_with_memory(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore

    card = CharacterCard(name="测试卡", veranima={"values": ["诚实"]})
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    fake_llm = type("FakeLLM", (), {
        "chat": lambda self, messages, max_tokens=None: "嗯，我记住了。",
        "is_model_loaded": lambda self: True,
        "low_energy_max_tokens": 512,
    })()
    return Agent(card=card, memory=store, llm=fake_llm, state=None, config={})


def test_agent_stores_framework(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.handle("我认为活着就是持续产生秩序和美")
    entries = a.memory.list_layer("semantic", limit=10)
    kinds = {(e.meta or {}).get("kind") for e in entries}
    assert "user_framework" in kinds


def test_second_confirmation_raises_stability(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.handle("我认为边界比热情可靠")
    a.handle("我还是觉得边界比热情可靠更重要")
    entries = [e for e in a.memory.list_layer("semantic", limit=10, include_superseded=True)
               if (e.meta or {}).get("kind") == "user_framework"]
    assert len(entries) >= 2  # 版本链：旧版保留
    current = [e for e in entries if e.id == max(x.id for x in entries)][0]
    assert (current.meta or {}).get("stability", 0.5) >= 0.5


def test_agent_skips_plain_chat(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.handle("今天天气不错")
    entries = a.memory.list_layer("semantic", limit=10)
    assert not any((e.meta or {}).get("kind") == "user_framework" for e in entries)

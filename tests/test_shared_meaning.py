"""P-2 共同意义（PERSONA_LOOP_SPEC P-2）：事件+双方解释 → shared_meaning，不伪造共识。"""
from __future__ import annotations

import pytest

from veranima.core.persona import build_shared_meaning_candidate, extract_shared_meaning_candidates
from veranima.core.persona import PersonaCandidate, persona_candidate_to_memory, validate_persona_candidate


# ---------- build_shared_meaning_candidate ----------

def test_full_agreement_ok():
    c = build_shared_meaning_candidate(
        event_summary="用户批评角色只是LLM复合体",
        user_interpretation="调侃，不是否定关系",
        character_interpretation="当时理解为否定",
        evidence_ids=[10],
        user_confirmed=True,
    )
    assert c is not None
    assert c.kind == "shared_meaning"
    assert c.needs_confirmation is False
    assert c.user_confirmed is True
    assert c.role_compatible is True


def test_missing_evidence_rejected():
    c = build_shared_meaning_candidate(
        event_summary="某事件", user_interpretation="解释A", character_interpretation="解释B",
        evidence_ids=[], user_confirmed=True,
    )
    assert c is None


def test_missing_interpretation_is_candidate():
    """缺任一方解释 → needs_confirmation=True，不伪造共识。"""
    c = build_shared_meaning_candidate(
        event_summary="共同完成小说初稿", user_interpretation="", character_interpretation="",
        evidence_ids=[5],
    )
    assert c is not None
    assert c.needs_confirmation is True
    assert c.user_confirmed is False
    assert c.content  # 至少记录事件


def test_disagreement_preserved():
    """双方解释不同且未确认 → agreed_meaning 不填。"""
    c = build_shared_meaning_candidate(
        event_summary="聊到深夜", user_interpretation="很愉快",
        character_interpretation="有些累但值得", evidence_ids=[7], user_confirmed=False,
    )
    assert c.needs_confirmation is True
    assert c.content  # 事件仍在


# ---------- extract_shared_meaning_candidates ----------

def test_extract_user_explains_past_event():
    cands = extract_shared_meaning_candidates(
        "上次我们聊到猫的时候，我觉得那是在说我需要陪伴", 21,
    )
    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "shared_meaning"
    assert "猫" in c.content
    assert c.evidence_message_ids == [21]


def test_extract_requires_past_event_marker():
    assert extract_shared_meaning_candidates("我觉得今天天气不错", 22) == []


def test_extract_requires_interpretation():
    assert extract_shared_meaning_candidates("上次我们一起去了咖啡馆", 23) == []


# ---------- 转换与校验 ----------

def test_conversion_shared_meaning():
    c = PersonaCandidate(kind="shared_meaning", title="t", content="x", evidence_message_ids=[1])
    mc = persona_candidate_to_memory(c, source_message_id=1)
    assert mc is not None
    assert mc["kind"] == "shared_meaning"
    assert mc["layer"] == "episodic"
    assert mc["confidence"] == 0.65


def test_validate_requires_evidence():
    c = PersonaCandidate(kind="shared_meaning", title="t", content="x", evidence_message_ids=[])
    assert any("evidence" in i for i in validate_persona_candidate(c, None))


# ---------- agent 集成 ----------

def _agent_with_memory(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore

    card = CharacterCard(name="测试卡", veranima={})
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    fake_llm = type("FakeLLM", (), {
        "chat": lambda self, messages, max_tokens=None: "嗯，我明白了。",
        "is_model_loaded": lambda self: True,
        "low_energy_max_tokens": 512,
    })()
    return Agent(card=card, memory=store, llm=fake_llm, state=None, config={})


def test_agent_stores_shared_meaning(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.handle("上次我们聊到猫的时候，我觉得那是在说我需要陪伴")
    entries = a.memory.list_layer("episodic", limit=10)
    kinds = {(e.meta or {}).get("kind") for e in entries}
    assert "shared_meaning" in kinds


def test_agent_plain_chat_no_shared_meaning(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.handle("猫真可爱")
    entries = a.memory.list_layer("episodic", limit=10)
    assert not any((e.meta or {}).get("kind") == "shared_meaning" for e in entries)

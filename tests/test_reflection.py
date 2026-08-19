"""P-5 反思整合（PERSONA_LOOP_SPEC P-5）：低频反思、程序校验、SelfModel 版本化。"""
from __future__ import annotations

import pytest

from veranima.core.reflection import (
    PersonaReflection,
    apply_reflection,
    propose_reflection,
    reflection_due,
    validate_reflection,
)


def _card():
    from veranima.core.character import CharacterCard
    return CharacterCard(name="测试卡", veranima={
        "core_drives": ["想理解用户"],
        "value_order": ["关系诚实", "独立判断"],
        "taboos": ["伤害他人"],
    })


# ---------- 触发条件 ----------

def test_due_only_on_known_triggers():
    counters = {"persona_candidates": 0, "high_emotion_events": 0, "user_corrections": 0}
    assert reflection_due("persona_candidates_20", counters) is False  # 计数不足
    counters["persona_candidates"] = 20
    assert reflection_due("persona_candidates_20", counters) is True
    assert reflection_due("high_emotion_event", counters) is True
    assert reflection_due("conflict_repaired", counters) is True


def test_not_due_on_plain_message():
    counters = {"persona_candidates": 5}
    assert reflection_due("user_message", counters) is False
    assert reflection_due("tick", counters) is False


def test_correction_trigger():
    counters = {"user_corrections": 0}
    assert reflection_due("user_correction", counters) is True  # 用户纠正 → 立即反思


# ---------- propose ----------

def test_propose_from_shared_meaning_evidence():
    evidence = [
        {"id": 1, "kind": "shared_meaning", "content": "共同事件：深夜长谈。用户解释：很愉快。", "confidence": 0.7},
    ]
    r = propose_reflection(evidence)
    assert isinstance(r, PersonaReflection)
    assert r.evidence_ids == [1]
    assert r.status == "proposed"
    assert r.confidence > 0


def test_propose_empty_evidence_rejected():
    assert propose_reflection([]) is None
    assert propose_reflection([{"id": 2, "kind": "user_fact", "content": "x"}]) is not None


# ---------- validate ----------

def test_validate_rejects_core_conflict():
    r = PersonaReflection(
        evidence_ids=[1], observed_change="认为可以伤害他人来保护用户",
        self_model_update={"learned_beliefs": ["伤害他人是必要的"]},
        relationship_update={}, user_model_update={}, unresolved_tension="",
        confidence=0.6, proposed_at="now", status="proposed",
    )
    issues = validate_reflection(r, _card())
    assert any("核心" in i or "taboo" in i or "禁忌" in i for i in issues)


def test_validate_ok():
    r = PersonaReflection(
        evidence_ids=[1], observed_change="用户偏好直接沟通",
        self_model_update={"learned_beliefs": ["用户重视直接沟通"]},
        relationship_update={"trust": 0.55}, user_model_update={}, unresolved_tension="",
        confidence=0.6, proposed_at="now", status="proposed",
    )
    assert validate_reflection(r, _card()) == []


def test_validate_requires_evidence():
    r = PersonaReflection(
        evidence_ids=[], observed_change="x", self_model_update={},
        relationship_update={}, user_model_update={}, unresolved_tension="",
        confidence=0.6, proposed_at="now", status="proposed",
    )
    assert any("evidence" in i for i in validate_reflection(r, _card()))


# ---------- apply ----------

def test_apply_updates_single_field_and_versions():
    models = {"self_model": {"version": 1, "learned_beliefs": []}, "relationship": {"trust": 0.5}}
    r = PersonaReflection(
        evidence_ids=[1], observed_change="形成新理解",
        self_model_update={"learned_beliefs": ["用户重视边界"]},
        relationship_update={}, user_model_update={}, unresolved_tension="",
        confidence=0.6, proposed_at="now", status="validated",
    )
    applied = apply_reflection(r, dict(models))
    assert applied["self_model"]["version"] == 2
    assert applied["self_model"]["learned_beliefs"] == ["用户重视边界"]
    assert applied["relationship"]["trust"] == 0.5  # 未更新的字段不动


def test_apply_does_not_change_on_validation_failure():
    models = {"self_model": {"version": 1, "learned_beliefs": []}, "relationship": {}}
    r = PersonaReflection(
        evidence_ids=[1], observed_change="x",
        self_model_update={"learned_beliefs": ["伤害他人是必要的"]},
        relationship_update={}, user_model_update={}, unresolved_tension="",
        confidence=0.6, proposed_at="now", status="rejected",
    )
    applied = apply_reflection(r, dict(models))
    assert applied["self_model"]["version"] == 1
    assert applied["self_model"]["learned_beliefs"] == []


def test_apply_does_not_modify_stable_traits():
    models = {"self_model": {"version": 1, "stable_traits": ["理性但护短"], "learned_beliefs": []}, "relationship": {}}
    r = PersonaReflection(
        evidence_ids=[1], observed_change="x",
        self_model_update={"stable_traits": ["变成另一个人"], "learned_beliefs": []},
        relationship_update={}, user_model_update={}, unresolved_tension="",
        confidence=0.6, proposed_at="now", status="validated",
    )
    applied = apply_reflection(r, dict(models))
    assert applied["self_model"]["stable_traits"] == ["理性但护短"]  # 稳定特征不可自动改


# ---------- agent 集成 ----------

def _agent(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore
    card = CharacterCard(name="测试卡", veranima={"value_order": ["关系诚实"]})
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    fake_llm = type("FakeLLM", (), {
        "chat": lambda self, messages, max_tokens=None: "嗯。",
        "is_model_loaded": lambda self: True,
        "low_energy_max_tokens": 512,
    })()
    return Agent(card=card, memory=store, llm=fake_llm, state=None, config={})


def test_agent_reflection_counter_grows(tmp_path):
    a = _agent(tmp_path)
    before = a._reflection_counters["persona_candidates"]
    a.handle("我认为活着就是产生秩序和美")
    assert a._reflection_counters["persona_candidates"] > before

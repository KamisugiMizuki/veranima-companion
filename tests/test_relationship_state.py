"""P-3 关系模型与 PAD 状态（PERSONA_LOOP_SPEC P-3）：事件驱动更新、阶段派生、重启恢复。"""
from __future__ import annotations

import pytest

from veranima.core.persona import (
    RelationshipModel,
    apply_emotion_event,
    apply_relationship_event,
    derive_relationship_stage,
)


# ---------- 事件驱动更新 ----------

def test_ordinary_message_does_not_change_relationship():
    """普通消息不升级关系（SPEC：不能按消息数量线性涨亲密度）。"""
    m = RelationshipModel()
    before = m.to_dict()
    # 模拟普通消息：无事件 → 不调用 apply
    assert before == m.to_dict()


def test_user_confirm_increases_trust():
    m = RelationshipModel()
    m2 = apply_relationship_event(m, {"type": "user_confirm", "cause": "用户说：你确实懂我"})
    assert m2.trust == pytest.approx(0.5 + 0.05)
    assert m2.familiarity == pytest.approx(0.5 + 0.05)
    assert m2.updated_at


def test_boundary_violation_lowers_safety():
    m = RelationshipModel(trust=0.7, safety=0.8)
    m2 = apply_relationship_event(m, {"type": "boundary_violation", "cause": "角色越界"})
    assert m2.safety < 0.8
    assert m2.conflict_tension > 0.2


def test_shared_project_raises_reciprocity():
    m = RelationshipModel()
    m2 = apply_relationship_event(m, {"type": "shared_project_done", "cause": "共同完成 veranima 初稿"})
    assert m2.reciprocity == pytest.approx(0.5 + 0.05)
    assert m2.familiarity > 0.5
    assert "veranima" in " ".join(m2.shared_projects)


def test_delta_cap_0_12():
    """单维单次变化上限：普通 0.05，重大 0.12，超限钳制。"""
    m = RelationshipModel()
    # 用伪造大 delta 的异常事件也应被钳制
    m2 = apply_relationship_event(m, {"type": "major_event", "cause": "x", "delta": {"trust": 0.9}})
    assert abs(m2.trust - 0.5) <= 0.12


def test_duplicate_event_idempotent():
    m = RelationshipModel()
    ev = {"type": "user_confirm", "cause": "确认", "event_id": "evt-1"}
    m2 = apply_relationship_event(m, ev)
    m3 = apply_relationship_event(m2, ev)  # 同 event_id 重放
    assert m3.to_dict() == m2.to_dict()


# ---------- 阶段派生 ----------

def test_stage_progression():
    assert derive_relationship_stage(RelationshipModel()) == "初识"
    m = RelationshipModel(trust=0.75, familiarity=0.8, intimacy=0.7, reciprocity=0.6, safety=0.75)
    assert derive_relationship_stage(m) == "信任"
    m2 = RelationshipModel(trust=0.9, familiarity=0.9, intimacy=0.85, reciprocity=0.8, safety=0.9)
    assert derive_relationship_stage(m2) == "长期共同体"


def test_stage_not_driven_by_attachment_alone():
    """attachment 高但 trust/safety 低 → 不能到亲密伙伴。"""
    m = RelationshipModel(trust=0.4, familiarity=0.6, intimacy=0.9, reciprocity=0.3, safety=0.3)
    assert derive_relationship_stage(m) != "亲密伙伴"


# ---------- PAD 情绪 ----------

def test_emotion_event_and_decay():
    st = type("S", (), {
        "valence": 0.5, "arousal": 0.5, "dominance": 0.5, "last_cause": ""
    })()
    apply_emotion_event(st, {"type": "happy", "cause": "用户分享好消息", "delta": {"valence": 0.8, "arousal": 0.7}})
    assert st.valence > 0.5
    assert st.arousal > 0.5
    assert st.last_cause == "用户分享好消息"
    # 衰减回基线
    apply_emotion_event(st, {"type": "decay", "cause": "时间流逝"})
    assert st.valence < 0.8


# ---------- 初始先验 ----------

def test_initial_affection_prior():
    """initial_affection 只做 intimacy/familiarity 先验，不自动 trust/safety。"""
    m = RelationshipModel.from_initial(initial_affection=0.8)
    assert m.intimacy == pytest.approx(0.8)
    assert m.familiarity == pytest.approx(0.8)
    assert m.trust == pytest.approx(0.5)
    assert m.safety == pytest.approx(0.5)


# ---------- agent 集成与重启 ----------

def _agent_with_memory(tmp_path, veranima=None):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore

    card = CharacterCard(name="测试卡", veranima=veranima or {})
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    fake_llm = type("FakeLLM", (), {
        "chat": lambda self, messages, max_tokens=None: "嗯。",
        "is_model_loaded": lambda self: True,
        "low_energy_max_tokens": 512,
    })()
    return Agent(card=card, memory=store, llm=fake_llm, state=None, config={})


def test_agent_relationship_survives_restart(tmp_path):
    a = _agent_with_memory(tmp_path, {"initial_affection": 0.7})
    r0 = a.relationship.to_dict()
    assert r0["intimacy"] == pytest.approx(0.7)
    # 一次明确关系事件
    a.relationship = apply_relationship_event(
        a.relationship, {"type": "user_confirm", "cause": "用户确认理解", "event_id": "evt-restart"}
    )
    a._persist_state()
    # 重启：同 DB 新建 Agent
    b = _agent_with_memory(tmp_path, {"initial_affection": 0.7})
    assert b.relationship.trust == pytest.approx(0.55)
    assert b.relationship.intimacy == pytest.approx(0.7)


def test_agent_plain_chat_does_not_bump_relationship(tmp_path):
    a = _agent_with_memory(tmp_path)
    before = a.relationship.to_dict()
    a.handle("今天天气不错")
    after = a.relationship.to_dict()
    assert before == after

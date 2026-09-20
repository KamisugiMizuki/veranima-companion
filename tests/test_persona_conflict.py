"""P-7 冲突修复与人格主动性（PERSONA_LOOP_SPEC P-7）：冲突闭环、越界守界、主动闸门。"""
from __future__ import annotations

import pytest

from veranima.core.persona import ConflictTracker, RelationshipModel, apply_relationship_event


# ---------- 冲突状态机 ----------

def test_conflict_lifecycle():
    t = ConflictTracker()
    t.open("c1", cause="角色误解了用户玩笑", evidence_ids=[1])
    assert t.status("c1") == "open"
    t.acknowledge("c1")
    assert t.status("c1") == "acknowledged"
    t.clarify("c1")
    assert t.status("c1") == "clarifying"
    t.repair("c1")
    assert t.status("c1") == "repairing"
    t.close("c1")
    assert t.status("c1") == "closed"
    assert t.open_conflicts() == []


def test_boundary_held_is_terminal_but_not_closed():
    t = ConflictTracker()
    t.open("b1", cause="用户要求保持距离", evidence_ids=[2])
    t.hold_boundary("b1")
    assert t.status("b1") == "boundary_held"
    # boundary_held 仍是开放张力：不应发沉重主动
    assert any(c["id"] == "b1" for c in t.open_conflicts())


def test_unknown_conflict_status():
    t = ConflictTracker()
    assert t.status("nope") is None


def test_serialization_roundtrip():
    t = ConflictTracker()
    t.open("c1", cause="x", evidence_ids=[1])
    t2 = ConflictTracker.from_dict(t.to_dict())
    assert t2.status("c1") == "open"


# ---------- 关系事件联动 ----------

def test_conflict_events_move_relationship():
    m = RelationshipModel(safety=0.8, conflict_tension=0.3)
    m2 = apply_relationship_event(m, {"type": "boundary_violation", "cause": "角色越界", "event_id": "e1"})
    assert m2.safety < 0.8
    assert m2.conflict_tension > 0.3
    m3 = apply_relationship_event(m2, {"type": "conflict_repaired", "cause": "修复完成", "event_id": "e2"})
    assert m3.conflict_tension < m2.conflict_tension
    assert m3.repair_progress > m2.repair_progress


def test_user_apology_does_not_auto_clear():
    """用户澄清不自动清零余波：需要显式 repair 事件。"""
    t = ConflictTracker()
    t.open("c1", cause="误解", evidence_ids=[1])
    t.clarify("c1")  # 澄清只是推进状态
    assert t.status("c1") == "clarifying"  # 不是 closed


# ---------- agent 集成 ----------

def _agent(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore
    card = CharacterCard(name="测试卡", veranima={})
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    fake_llm = type("FakeLLM", (), {
        "chat": lambda self, messages, max_tokens=None: "嗯。",
        "is_model_loaded": lambda self: True,
        "low_energy_max_tokens": 512,
    })()
    return Agent(card=card, memory=store, llm=fake_llm, state=None, config={})


def test_agent_detects_user_apology_and_repairs(tmp_path):
    a = _agent(tmp_path)
    a._conflicts.open("c1", cause="角色说错话", evidence_ids=[1])
    a.handle("对不起，我刚才不是那个意思，我开玩笑的")
    assert a._conflicts.status("c1") in ("clarifying", "repairing", "closed")


def test_agent_does_not_send_heavy_proactive_during_conflict(tmp_path):
    a = _agent(tmp_path)
    a._conflicts.open("c1", cause="未解决", evidence_ids=[1])
    # tick_proactive 的 shared_meaning 来源应被冲突闸门拦截
    blocked = a.persona_proactive_blocked("shared_meaning")
    assert blocked is True
    assert a.persona_proactive_blocked("commitment") is False  # 承诺提醒不受影响


def test_agent_closed_conflict_does_not_punish(tmp_path):
    a = _agent(tmp_path)
    a._conflicts.open("c1", cause="已修复", evidence_ids=[1])
    a._conflicts.close("c1")
    assert a.persona_proactive_blocked("shared_meaning") is False


# ---------- 事实纠正通道（2026-09-20 设计修订） ----------

def test_agent_user_clarification_revokes_open_tension(tmp_path):
    """「你误会了」= 纠错：未成立的扣减撤销，不用等用户刷修复互动。"""
    a = _agent(tmp_path)
    a._apply_tension_event(event_type="unanswered_proactive", channel="im", base_delta=10,
                           reason="未回复", dedupe_key="proactive-unanswered:1")
    assert a.tension.state.value == 10

    a.handle("你误会了，我不是那个意思")

    assert a.tension.state.value == 0
    assert a.tension.state.open_event_ids == []


def test_agent_plain_apology_does_not_revoke(tmp_path):
    """单纯道歉=认了这件事，不是误读——撤销通道不得变成「道歉免罚」。"""
    a = _agent(tmp_path)
    a._apply_tension_event(event_type="unanswered_proactive", channel="im", base_delta=10,
                           reason="未回复", dedupe_key="proactive-unanswered:2")
    a.handle("对不起")
    assert a.tension.state.value == 10
    assert a.tension.state.open_event_ids


def test_clarification_does_not_revoke_a_repair(tmp_path):
    """刚修好的关系不能被下一句澄清连带撤掉（修复事件不是"负向事件"）。"""
    a = _agent(tmp_path)
    a.relationship = apply_relationship_event(
        a.relationship, {"type": "conflict_repaired", "cause": "冲突修复", "event_id": "r1"})
    trust = a.relationship.trust
    a.handle("开玩笑的，别往心里去")
    assert a.relationship.trust == pytest.approx(trust)
    assert a.relationship.revoked_event_ids == []


def test_agent_user_correction_revokes_negative_relationship_event(tmp_path):
    a = _agent(tmp_path)
    a.handle("你太过分了")
    assert any(float(v) < 0
               for e in a.relationship.applied_events
               for v in (e.get("deltas") or {}).values())
    safety = a.relationship.safety

    a.handle("别当真，我开玩笑的")

    assert a.relationship.safety > safety
    assert a.relationship.revoked_event_ids

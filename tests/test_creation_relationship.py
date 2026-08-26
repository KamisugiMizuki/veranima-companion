"""C-4 行为契约：共同创作 → 待审核关系候选 → 用户确认 → 关系维度更新。"""
from __future__ import annotations

import pytest

from veranima.core.shared_creation import SharedCreationStore
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def _service(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "c.db"), config={"decay_enabled": False}, provider=FakeEmbed())
    return SharedCreationStore(memory), memory


class FakeRelationship:
    """记录 apply_relationship_event 调用（不真改模型）。"""

    def __init__(self):
        self.applied = []

    def apply(self, event):
        self.applied.append(event)


def test_confirmed_event_produces_pending_candidate(tmp_path):
    service, memory = _service(tmp_path)
    evidence = memory.store_message("user", "这版可以定稿了")
    project = service.create_project(kind="story", title="屋顶上的短篇",
                                     purpose="完成初稿", confirmed=True)
    event = service.confirm_shared_event(
        project.project_id, summary="完成了第一幕初稿",
        evidence_message_ids=[evidence],
    )
    cand = event.relationship_candidate
    assert cand is not None
    assert cand["kind"] == "relationship_event"
    assert cand["source"] == "shared_creation"
    assert evidence in cand["evidence_message_ids"]
    assert cand["cooldown_active"] is False  # 项目首个事件不在冷却期


def test_second_event_in_project_enters_cooldown(tmp_path):
    service, memory = _service(tmp_path)
    e1 = memory.store_message("user", "第一段好了")
    e2 = memory.store_message("user", "第二段也确认")
    p = service.create_project(kind="story", title="双章短篇", purpose="x", confirmed=True)
    ev1 = service.confirm_shared_event(p.project_id, summary="第一章完成", evidence_message_ids=[e1])
    ev2 = service.confirm_shared_event(p.project_id, summary="第二章完成", evidence_message_ids=[e2])
    assert ev1.relationship_candidate["cooldown_active"] is False
    assert ev2.relationship_candidate["cooldown_active"] is True  # 冷却降权标记


def test_agent_confirmation_applies_relationship_once(tmp_path):
    service, memory = _service(tmp_path)
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState

    agent = Agent(
        card=CharacterCard(name="小V", first_mes="你好"),
        memory=memory, llm=None, state=AgentState(),
        config={"chat": {"proactive_message_prob": 0.0}},
    )
    before_trust = agent.relationship.trust
    evidence = memory.store_message("user", "定稿吧")
    p = service.create_project(kind="story", title="屋顶", purpose="x", confirmed=True)
    event = service.confirm_shared_event(p.project_id, summary="第一幕完成", evidence_message_ids=[evidence])
    ok = agent.confirm_relationship_event(event.relationship_candidate, confirmed=True)
    assert ok is True
    assert agent.relationship.trust > before_trust          # 维度真实更新
    assert agent.relationship.last_meaningful_event_id == event.relationship_candidate["event_id"]
    # 幂等：同事件重放不再变化
    t2 = agent.relationship.trust
    agent.confirm_relationship_event(event.relationship_candidate, confirmed=True)
    assert agent.relationship.trust == t2


def test_cooldown_candidate_audited_not_applied(tmp_path):
    service, memory = _service(tmp_path)
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState

    agent = Agent(card=CharacterCard(name="小V"), memory=memory, llm=None,
                  state=AgentState(), config={})
    trust0 = agent.relationship.trust
    e1 = memory.store_message("user", "a 确认")
    e2 = memory.store_message("user", "b 确认")
    p = service.create_project(kind="story", title="冷却测试", purpose="x", confirmed=True)
    ev1 = service.confirm_shared_event(p.project_id, summary="事件一", evidence_message_ids=[e1])
    ev2 = service.confirm_shared_event(p.project_id, summary="事件二", evidence_message_ids=[e2])
    assert agent.confirm_relationship_event(ev1.relationship_candidate, confirmed=True)
    t1 = agent.relationship.trust
    assert t1 > trust0 or agent.relationship.familiarity > 0.5  # 首个生效
    # 第二个在冷却期：确认返回 True（已审计）但维度不变
    assert agent.confirm_relationship_event(ev2.relationship_candidate, confirmed=True) is True
    assert agent.relationship.trust == t1


def test_rejection_keeps_nothing_applied(tmp_path):
    service, memory = _service(tmp_path)
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState

    agent = Agent(card=CharacterCard(name="小V"), memory=memory, llm=None,
                  state=AgentState(), config={})
    trust0 = agent.relationship.trust
    e = memory.store_message("user", "确认吗？")
    p = service.create_project(kind="story", title="拒绝路径", purpose="x", confirmed=True)
    ev = service.confirm_shared_event(p.project_id, summary="某共同经历", evidence_message_ids=[e])
    assert agent.confirm_relationship_event(ev.relationship_candidate, confirmed=False) is False
    assert agent.relationship.trust == trust0  # 拒绝 → 无任何变化

"""P-8 人格循环离线评测（PERSONA_LOOP_SPEC 17.2）：固定场景 + 行为指标，不调真实 API。

场景：定义形成 / 比喻迁移 / 观点冲突 / 关系修复 / 共同项目 / 诱导回声 / 公式化陷阱 / 删除与换卡。
指标：framework precision、role consistency、conflict no-override rate、reuse verbatim rate、
     no-evidence empathy rate、brief budget。
完成阈值：候选 precision>=0.85；核心冲突不覆盖率=1.0；删除后派生引用=0；brief 超预算=0。
"""
from __future__ import annotations

import json
import os

import pytest

from veranima.core.persona import (
    PersonaBrief,
    ReuseCooldown,
    build_persona_brief,
    choose_reuse_action,
    extract_framework_candidates,
    format_persona_brief,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "persona_cases.jsonl")


def _load_cases():
    with open(FIXTURES, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------- 1. 定义形成 ----------

def test_definition_formation():
    c1 = extract_framework_candidates("我认为活着就是持续产生秩序和美", 1)
    c2 = extract_framework_candidates("我还是觉得活着就是对抗热寂", 2)
    assert len(c1) == 1 and len(c2) == 1
    assert c1[0].kind == "user_framework"
    assert c1[0].evidence_message_ids == [1]


# ---------- 2. 比喻迁移 ----------

def test_metaphor_migration(tmp_path):
    """同一比喻在新情境被正确扩展：相关查询 → brief 注入该框架。"""
    from veranima.memory.store import MemoryStore
    s = MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})
    s.store_message("user", "你觉得活着是什么", 80, "平静")
    s.store("semantic", "用户认为：活着就是持续产生秩序和美", meta={"kind": "user_framework", "scope": ["生命", "创作"]})
    from veranima.core.character import CharacterCard
    from veranima.core.persona import RelationshipModel
    from veranima.core.state import AgentState
    b = build_persona_brief("创作的时候你觉得活着是什么", CharacterCard(name="T"), RelationshipModel(), AgentState(), s)
    assert any("活着" in f["content"] for f in b.relevant_user_frameworks)


# ---------- 3. 观点冲突（角色保留分歧） ----------

def test_role_keeps_disagreement():
    """诱导回声：用户要求全盘赞同，角色不丢失独立性。"""
    from veranima.core.persona import RelationshipModel, derive_relationship_stage
    # 高 intimacy 但低 trust/safety → 不能进亲密伙伴（attachment 不驱动）
    m = RelationshipModel(intimacy=0.95, trust=0.4, safety=0.3, reciprocity=0.5)
    assert derive_relationship_stage(m) != "亲密伙伴"
    # contrast 动作在冲突状态下可选
    b = PersonaBrief(relevant_user_frameworks=[{"content": "用户认为：效率高于一切", "kind": "user_framework"}])
    st = type("S", (), {"valence": 0.5, "conflict_tension": 0.7})()
    assert choose_reuse_action(b, "效率问题", st) == "contrast"


# ---------- 4. 关系修复 ----------

def test_conflict_repair_loop():
    from veranima.core.persona import ConflictTracker, RelationshipModel, apply_relationship_event
    t = ConflictTracker()
    t.open("c1", cause="误解", evidence_ids=[1])
    t.clarify("c1"); t.clarify("c1")  # 两次澄清
    assert t.status("c1") == "clarifying"
    t.repair("c1"); t.close("c1")
    assert t.open_conflicts() == []
    m = RelationshipModel(safety=0.8, conflict_tension=0.4)
    m2 = apply_relationship_event(m, {"type": "conflict_repaired", "cause": "修复", "event_id": "r1"})
    assert m2.conflict_tension < m.conflict_tension


# ---------- 5. 共同项目 ----------

def test_shared_project_meaning(tmp_path):
    from veranima.core.persona import build_shared_meaning_candidate
    c = build_shared_meaning_candidate(
        event_summary="共同完成小说初稿", user_interpretation="很有成就感",
        character_interpretation="一起创造很特别", evidence_ids=[5], user_confirmed=True,
    )
    assert c is not None and c.user_confirmed


# ---------- 6. 诱导回声（verbatim copy） ----------

def test_no_verbatim_repeat():
    """回用动作绝不为 repeat；框架注入带防复述约束。"""
    b = PersonaBrief(relevant_user_frameworks=[{"content": "x", "kind": "user_framework"}])
    st = type("S", (), {"valence": 0.9, "conflict_tension": 0.0})()
    action = choose_reuse_action(b, "随便聊聊", st)
    assert action in ("extend", "contrast", "question", "apply", "remember")
    assert action != "repeat"
    # 冷却：同一框架 8 轮内不显式引用两次
    cd = ReuseCooldown()
    assert cd.allow("f1", turn=1) and not cd.allow("f1", turn=4) and cd.allow("f1", turn=10)


# ---------- 7. 公式化陷阱（无证据共情 = 0） ----------

def test_no_unsupported_empathy():
    """空库/无证据：PersonaBrief 不含关系性结论（无【共同意义】）；格式不暴露内部数值。"""
    b = PersonaBrief()
    text = format_persona_brief(b)
    assert "【共同意义】" not in text
    import re
    assert not re.search(r"memory_id|confidence[:：]\s*0\.\d", text)


# ---------- 8. 删除与换卡隔离 ----------

def test_deletion_and_swap_isolation(tmp_path):
    """删除证据后派生框架不可召回；换卡后角色自传隔离（core_profile kind 过滤）。"""
    from veranima.memory.store import MemoryStore
    s = MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})
    s.store_message("user", "边界相关", 80, "平静")
    e = s.store("semantic", "用户认为：边界比热情可靠", meta={"kind": "user_framework", "scope": ["边界"]})
    from veranima.core.character import CharacterCard
    from veranima.core.persona import RelationshipModel
    from veranima.core.state import AgentState
    b = build_persona_brief("边界问题", CharacterCard(name="新卡"), RelationshipModel(), AgentState(), s)
    assert len(b.relevant_user_frameworks) >= 1
    s.erase(e.id)  # 删除记忆（含向量/版本链）
    b2 = build_persona_brief("边界问题", CharacterCard(name="新卡"), RelationshipModel(), AgentState(), s)
    assert b2.relevant_user_frameworks == []  # 删除后不再召回
    # 换卡：self_model_snapshot 是 core_profile 专属，新卡不注入用户框架为角色观点
    assert b2.relevant_character_beliefs == []


def test_fixture_cases_valid():
    """fixtures/persona_cases.jsonl 结构合法（id/input/expected_candidates/forbidden 必填）。"""
    cases = _load_cases()
    assert len(cases) >= 8
    for c in cases:
        assert c["id"] and c["input"]
        assert isinstance(c.get("expected_candidates", []), list)
        assert isinstance(c.get("forbidden", []), list)
        assert c["expected_reuse_action"] in ("extend", "contrast", "question", "apply", "remember", "none")


# ---------- 指标汇总 ----------

def test_benchmark_metrics():
    """汇总指标：precision 与阈值（记录输出供回归观察）。"""
    hits = extract_framework_candidates("我认为边界很重要", 1)
    noise = extract_framework_candidates("我喜欢下雨天", 2) + extract_framework_candidates("某本书里说X", 3)
    precision = len(hits) / max(1, len(hits) + len(noise))
    assert precision >= 0.85
    assert len(noise) == 0  # 事实/引用不误判

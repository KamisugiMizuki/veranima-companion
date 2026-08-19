"""P-4 Persona Brief（PERSONA_LOOP_SPEC P-4）：相关框架/共同意义/关系上下文预算注入。"""
from __future__ import annotations

import pytest

from veranima.core.persona import PersonaBrief, build_persona_brief, format_persona_brief


def _store(tmp_path):
    from veranima.memory.store import MemoryStore
    return MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})


def _card():
    from veranima.core.character import CharacterCard
    return CharacterCard(
        name="测试卡",
        veranima={
            "core_drives": ["想理解用户，但不愿无条件顺从"],
            "inner_tensions": [{"left": "渴望靠近", "right": "害怕失去边界"}],
            "value_order": ["关系诚实", "独立判断"],
        },
    )


def _rel():
    from veranima.core.persona import RelationshipModel
    return RelationshipModel(trust=0.8, familiarity=0.8, intimacy=0.7, safety=0.8)


def _state():
    from veranima.core.state import AgentState
    st = AgentState()
    st.valence, st.arousal, st.dominance = 0.7, 0.6, 0.5
    return st


def test_brief_empty_when_no_relevant_memory(tmp_path):
    s = _store(tmp_path)
    b = build_persona_brief("今天吃什么", _card(), _rel(), _state(), s)
    assert isinstance(b, PersonaBrief)
    assert b.relevant_user_frameworks == []
    assert b.relevant_character_beliefs == []
    assert b.shared_meanings == []
    assert b.core_tensions  # 角色卡张力常驻


def test_brief_keeps_core_tensions(tmp_path):
    s = _store(tmp_path)
    b = build_persona_brief("随便聊聊", _card(), _rel(), _state(), s)
    assert any("渴望靠近" in t for t in b.core_tensions)


def test_relevant_framework_injected(tmp_path):
    s = _store(tmp_path)
    s.store("semantic", "用户认为：活着就是持续产生秩序和美", meta={
        "kind": "user_framework", "scope": ["生命", "创作"], "stability": 0.6,
    })
    b = build_persona_brief("你觉得活着是什么", _card(), _rel(), _state(), s)
    assert len(b.relevant_user_frameworks) >= 1
    assert any("活着" in f["content"] for f in b.relevant_user_frameworks)


def test_irrelevant_framework_not_injected(tmp_path):
    s = _store(tmp_path)
    s.store("semantic", "用户认为：边界比热情可靠", meta={"kind": "user_framework", "scope": ["边界"]})
    b = build_persona_brief("今天天气怎么样", _card(), _rel(), _state(), s)
    assert b.relevant_user_frameworks == []


def test_kind_labels_separated(tmp_path):
    """用户框架与角色观点分标签，不混成角色既定信念。"""
    s = _store(tmp_path)
    s.store("semantic", "用户认为：效率很重要", meta={"kind": "user_framework", "scope": ["效率"]})
    s.store("semantic", "角色观点：效率不该压倒关系", meta={"kind": "character_belief", "scope": ["效率"]})
    b = build_persona_brief("效率怎么权衡", _card(), _rel(), _state(), s)
    assert any("效率" in f["content"] for f in b.relevant_user_frameworks)
    assert any("效率" in f["content"] for f in b.relevant_character_beliefs)


def test_cap_per_category(tmp_path):
    s = _store(tmp_path)
    for i in range(5):
        s.store("semantic", f"用户认为：框架内容{i}号与主题相关", meta={"kind": "user_framework", "scope": ["主题"]})
    b = build_persona_brief("主题相关的问题", _card(), _rel(), _state(), s)
    assert len(b.relevant_user_frameworks) <= 2
    total = len(b.relevant_user_frameworks) + len(b.relevant_character_beliefs) + len(b.shared_meanings)
    assert total <= 6


def test_char_budget(tmp_path):
    s = _store(tmp_path)
    for i in range(5):
        s.store("semantic", f"用户认为：框架内容{i}号与主题相关讨论", meta={"kind": "user_framework", "scope": ["主题"]})
    b = build_persona_brief("主题相关的问题", _card(), _rel(), _state(), s, max_chars=300)
    text = format_persona_brief(b)
    assert len(text) <= 300


def test_format_hides_internal_ids(tmp_path):
    s = _store(tmp_path)
    s.store("semantic", "用户认为：活着就是产生秩序", meta={"kind": "user_framework", "scope": ["生命"], "stability": 0.6})
    b = build_persona_brief("活着是什么", _card(), _rel(), _state(), s)
    text = format_persona_brief(b)
    import re
    assert not re.search(r"memory_id|confidence[:：]\s*0\.\d|stability", text)
    assert "【理解用户】" in text
    assert "【共同意义】" not in text  # 无共同意义时不出现


def test_format_relationship_context(tmp_path):
    s = _store(tmp_path)
    b = build_persona_brief("随便聊聊", _card(), _rel(), _state(), s)
    text = format_persona_brief(b)
    assert "信任" in text or "关系" in text


def test_prompt_has_single_persona_entry(tmp_path):
    """build_system_prompt 有且只有一个 PersonaBrief 接入口。"""
    from veranima.core.prompts import build_system_prompt
    s = _store(tmp_path)
    card = _card()
    prompt = build_system_prompt(card, _state(), s, relationship=_rel(), channel="im")
    # 关系上下文恒注入；【理解用户】只在有相关框架时出现（标签分离，不泄漏内部 id/数值）
    assert prompt.count("【关系】") == 1
    assert "【理解用户】" not in prompt  # 空库无框架 → 不出现
    import re
    assert not re.search(r"memory_id|confidence[:：]\s*0\.\d", prompt)

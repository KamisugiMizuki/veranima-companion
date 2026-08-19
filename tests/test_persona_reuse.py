"""P-6 回用与防回声室（PERSONA_LOOP_SPEC P-6）：动作选择、冷却、不逐字复述。"""
from __future__ import annotations

import pytest

from veranima.core.persona import PersonaBrief, ReuseCooldown, choose_reuse_action


def _brief(frameworks=None, meanings=None, beliefs=None) -> PersonaBrief:
    return PersonaBrief(
        core_tensions=["渴望靠近 / 害怕失去边界"],
        relevant_user_frameworks=frameworks or [],
        relevant_character_beliefs=beliefs or [],
        shared_meanings=meanings or [],
    )


def _state(valence=0.5, arousal=0.5):
    from veranima.core.state import AgentState
    st = AgentState()
    st.valence, st.arousal, st.dominance = valence, arousal, 0.5
    return st


# ---------- choose_reuse_action ----------

def test_no_relevant_framework_none():
    b = _brief()
    assert choose_reuse_action(b, "今天天气怎么样", _state()) == "none"


def test_default_action_is_apply_not_repeat():
    b = _brief(frameworks=[{"content": "用户认为：活着就是持续产生秩序和美", "kind": "user_framework"}])
    action = choose_reuse_action(b, "你怎么看活着这件事", _state())
    assert action in ("extend", "contrast", "question", "apply", "remember")
    assert action != "repeat"  # 默认动作不是 repeat


def test_low_mood_prefers_question():
    b = _brief(frameworks=[{"content": "用户认为：边界比热情可靠", "kind": "user_framework"}])
    assert choose_reuse_action(b, "边界怎么看待", _state(valence=0.2)) == "question"


def test_conflict_prefers_contrast():
    b = _brief(frameworks=[{"content": "用户认为：效率高于一切", "kind": "user_framework"}])
    st = _state()
    st.conflict_tension = 0.7
    assert choose_reuse_action(b, "效率问题", st) == "contrast"


def test_shared_meaning_remember_when_direct():
    b = _brief(meanings=[{"content": "共同事件：深夜长谈。用户解释：很愉快。", "kind": "shared_meaning"}])
    assert choose_reuse_action(b, "还记得那晚吗", _state()) == "remember"


# ---------- cooldown ----------

def test_cooldown_blocks_within_8_turns():
    c = ReuseCooldown()
    assert c.allow("frame-1", turn=1) is True
    assert c.allow("frame-1", turn=5) is False   # 未满 8 轮
    assert c.allow("frame-2", turn=5) is True    # 不同框架不受影响
    assert c.allow("frame-1", turn=10) is True   # 满 8 轮恢复


def test_cooldown_tracks_last_use():
    c = ReuseCooldown()
    c.allow("f", turn=3)
    c.allow("f", turn=12)  # 冷却满 → 允许并更新时间戳
    assert c.allow("f", turn=15) is False  # 从 12 重新计时


# ---------- prompt 约束 ----------

def test_persona_brief_prompt_forbids_verbatim_copy(tmp_path):
    """【理解用户】块包含动作约束：扩展/对照/限定/应用，不逐字复述。"""
    from veranima.core.prompts import build_system_prompt
    from veranima.memory.store import MemoryStore
    from veranima.core.character import CharacterCard
    from veranima.core.persona import RelationshipModel
    from veranima.core.state import AgentState

    s = MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})
    s.store_message("user", "你觉得活着是什么", 80, "平静")
    s.store("semantic", "用户认为：活着就是持续产生秩序和美", meta={"kind": "user_framework", "scope": ["活着"]})
    card = CharacterCard(name="测试卡", veranima={})
    st = AgentState()
    prompt = build_system_prompt(card, st, s, relationship=RelationshipModel(), channel="im")
    assert "不要逐字复述用户原句" in prompt
    assert "扩展" in prompt or "对照" in prompt or "应用" in prompt

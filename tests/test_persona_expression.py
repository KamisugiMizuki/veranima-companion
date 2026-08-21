"""P-9 表达控制面（PERSONA_LOOP_SPEC P-9）：ResponsePlan、PAD 映射、Imprint、有因差异。"""
from __future__ import annotations

import pytest

from veranima.core.persona import (
    ImprintTracker,
    PersonaBrief,
    ResponsePlan,
    build_response_plan,
    render_authenticity,
)


def _state(valence=0.5, arousal=0.5, dominance=0.5, conflict=0.0):
    from veranima.core.state import AgentState
    st = AgentState()
    st.valence, st.arousal, st.dominance = valence, arousal, dominance
    st.conflict_tension = conflict
    return st


# ---------- ResponsePlan ----------

def test_simple_fact_skips_plan():
    b = PersonaBrief()
    assert build_response_plan({"user_text": "现在几点"}, b, _state()) is None


def test_explicit_length_enables_plan_on_simple_turn():
    plan = build_response_plan(
        {"user_text": "普通问题", "explicit_style_length": "short"}, PersonaBrief(), _state(),
    )
    assert plan is not None and plan.desired_length == "short"


def test_response_plan_carries_relational_tension_hint():
    state = _state()
    state.relational_tension_band = "guarded"

    plan = build_response_plan({"user_text": "继续说"}, PersonaBrief(), state)

    assert plan is not None
    assert plan.tension_hint == "guarded"


def test_conflict_enables_plan():
    b = PersonaBrief(core_tensions=["渴望靠近 / 害怕失去边界"])
    p = build_response_plan({"user_text": "你为什么那样说"}, b, _state(conflict=0.7))
    assert p is not None
    assert p.intent in ("comfort", "challenge", "clarify", "reflect")


def test_framework_reuse_enables_plan():
    b = PersonaBrief(relevant_user_frameworks=[{"content": "用户认为：活着就是产生秩序", "kind": "user_framework"}])
    p = build_response_plan({"user_text": "活着对你意味着什么"}, b, _state())
    assert p is not None
    assert p.recalled_frame_ids


def test_plan_does_not_contain_cot():
    b = PersonaBrief(relevant_user_frameworks=[{"content": "x", "kind": "user_framework"}])
    p = build_response_plan({"user_text": "q"}, b, _state())
    assert len(p.opening_move) <= 50
    assert "我不应该" not in p.opening_move  # 无内心独白
    assert p.recalled_frame_ids  # 只含结构化字段


# ---------- render_authenticity ----------

def test_render_changes_style_not_facts():
    r = render_authenticity("用户喜欢下雨天，他昨天去了公园", {"valence": 0.8, "arousal": 0.7}, "im")
    assert "用户喜欢下雨天" in r["text"]  # 事实不变
    assert r["style_hint"] in ("short", "normal", "long")


def test_render_low_energy_shortens():
    r = render_authenticity("随便聊聊", {"valence": 0.2, "arousal": 0.2}, "im")
    assert r["style_hint"] == "short"


def test_render_high_arousal_short_bursts():
    r = render_authenticity("太好了我们成功了", {"valence": 0.9, "arousal": 0.9}, "im")
    assert r["style_hint"] == "short"


def test_render_tts_same_facts_shorter():
    text = "这是一个很长的事实陈述，包含很多细节和背景信息，需要完整说出来"
    im = render_authenticity(text, {"valence": 0.5, "arousal": 0.4}, "im")
    tts = render_authenticity(text, {"valence": 0.5, "arousal": 0.4}, "tts")
    assert im["text"] == tts["text"]  # 事实立场一致（文本层不重写）
    assert tts["style_hint"] in ("short", "normal")  # TTS 更口语短


# ---------- PersonaImprint ----------

def test_imprint_single_feedback_is_candidate():
    t = ImprintTracker()
    t.note("depth", direction=1.0, evidence=1)
    assert t.status("depth") == "candidate"


def test_imprint_cross_scene_becomes_active():
    t = ImprintTracker()
    t.note("depth", 1.0, evidence=1, scope="技术讨论")
    t.note("depth", 1.0, evidence=2, scope="技术讨论")
    t.note("depth", 1.0, evidence=3, scope="技术讨论")  # 3 次跨场景 → active
    assert t.status("depth") == "active"
    assert t.active_imprints() == [("depth", "技术讨论")]


def test_imprint_negative_feedback_blocks():
    t = ImprintTracker()
    t.note("depth", 1.0, evidence=1)
    t.note("depth", -1.0, evidence=2)  # 用户反感 → 拒绝
    assert t.status("depth") == "rejected"


def test_imprint_does_not_touch_core():
    t = ImprintTracker()
    t.note("humor", 1.0, evidence=1)
    assert t.to_dict()  # 印记独立存储，不修改 Character Core


# ---------- 有因差异 ----------

def test_association_requires_target():
    """自然联想必须有 association_target；无关联随机跑题不存在。"""
    from veranima.core.persona import build_response_plan
    b = PersonaBrief(shared_meanings=[{"content": "共同事件：深夜长谈", "kind": "shared_meaning"}])
    p = build_response_plan({"user_text": "还记得那晚吗"}, b, _state())
    assert p is not None
    assert p.association_target  # 有可追溯目标
    # 普通闲聊无共同意义 → 无联想
    b2 = PersonaBrief()
    assert build_response_plan({"user_text": "吃了吗"}, b2, _state()) is None


def test_no_random_personality_drift():
    """同一状态两次渲染结果一致（无固定概率随机反常）。"""
    r1 = render_authenticity("test", {"valence": 0.5, "arousal": 0.5}, "im")
    r2 = render_authenticity("test", {"valence": 0.5, "arousal": 0.5}, "im")
    assert r1 == r2


def test_persistent_short_reply_request_becomes_interaction_rule():
    from veranima.core.persona import extract_framework_candidates

    candidates = extract_framework_candidates("以后回复都简短点，只说结论。", 7)
    assert len(candidates) == 1
    assert candidates[0].kind == "interaction_rule"
    assert "简短" in candidates[0].content

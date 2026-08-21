from __future__ import annotations

from veranima.core.tension_events import (
    classify_user_tension_event,
    classify_low_investment_streak,
    extract_direct_question,
)


def test_explicit_pause_is_not_negative_tension():
    event = classify_user_tension_event("以后别主动找我")
    assert event is not None
    assert event.event_type == "explicit_pause"
    assert event.base_delta == 0


def test_new_user_conversation_reduces_tension():
    event = classify_user_tension_event("我回来啦，继续聊刚才的事", new_conversation=True)
    assert event.event_type == "user_initiated"
    assert event.base_delta == -5


def test_direct_question_can_be_tracked_without_guessing_skip():
    question = extract_direct_question("我问的是你后来有没有试那个方案？")
    assert question == "我问的是你后来有没有试那个方案？"
    assert classify_user_tension_event("我问的是你后来有没有试那个方案？", direct_question=question) is None


def test_related_answer_repairs_and_unrelated_answer_can_raise_candidate():
    question = "你后来有没有试那个方案？"
    repair = classify_user_tension_event("试了，昨天已经跑通了", direct_question=question)
    skipped = classify_user_tension_event("对了，天气还不错", direct_question=question)

    assert repair.event_type == "answered_question"
    assert repair.base_delta == -8
    assert skipped.event_type == "question_skipped"
    assert skipped.base_delta == 5


def test_short_reply_is_not_alone_enough_to_be_negative():
    assert classify_user_tension_event("嗯") is None


def test_three_low_investment_replies_need_expandable_context():
    rows = [
        {"role": "assistant", "content": "你最近怎么样，愿意详细说说吗？"},
        {"role": "user", "content": "嗯"},
        {"role": "assistant", "content": "那工作还顺利吗？"},
        {"role": "user", "content": "哦"},
        {"role": "assistant", "content": "这件事你怎么看？"},
    ]

    event = classify_low_investment_streak(rows, "好")

    assert event is not None
    assert event.event_type == "terse_streak"
    assert event.base_delta == 3


def test_simple_question_short_answer_does_not_make_streak():
    rows = [
        {"role": "assistant", "content": "现在几点？"},
        {"role": "user", "content": "嗯"},
        {"role": "assistant", "content": "今天下雨吗？"},
        {"role": "user", "content": "哦"},
    ]

    assert classify_low_investment_streak(rows, "好") is None

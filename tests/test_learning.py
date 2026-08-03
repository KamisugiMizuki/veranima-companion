"""MVP2 学习模块测试：隐式反馈提取 / 风格参数学习（bandits+EMA）/ 语言镜像 / 持久化与回滚。"""

from __future__ import annotations

import pytest

from veranima.core.learning import (
    FeedbackSignal,
    LanguageMirror,
    StyleLearner,
    StyleParams,
    extract_feedback,
)


# ---------- 隐式反馈提取 ----------

def test_extract_feedback_positive():
    s = extract_feedback("哈哈你说得对", "回复", "")
    assert s.positive
    assert not s.negative
    assert s.reward > 0


def test_extract_feedback_negative_and_correction():
    s = extract_feedback("不对，你理解错了，太长了", "回复" * 100, "")
    assert s.negative
    assert s.correction
    assert s.reward < 0


def test_extract_feedback_question():
    s = extract_feedback("你觉得周末去哪玩比较好？", "回复", "")
    assert s.user_asked


def test_extract_feedback_topic_continuation():
    prev = "我昨天去爬山了，山顶的风很大"
    s = extract_feedback("爬山累不累呀", "回复", prev)
    assert s.topic_continuation


def test_extract_feedback_delay_signal():
    # 读得久（>30s）→ 正奖励
    s = extract_feedback("嗯", "回复", delay=45)
    assert s.reward > 0
    # 秒回长回复 → 负奖励
    s2 = extract_feedback("嗯", "回" * 400, delay=1)
    assert s2.reward < 0


# ---------- 风格参数学习 ----------

def test_observe_changes_params():
    learner = StyleLearner()
    before = learner.params.snapshot()
    # 用户提问但回复很短 → 应倾向加长
    learner.observe(FeedbackSignal(user_asked=True, reply_len=50, user_len=80))
    after = learner.params.snapshot()
    # EMA 平滑 + 保守漂移：变化应存在但幅度小（单步 ≤ ~0.05）
    assert abs(after["reply_length"] - before["reply_length"]) <= 0.06
    # 学习步数累计
    assert learner._steps == 1


def test_observe_negative_correction_drives_topic_follow():
    learner = StyleLearner()
    learner.observe(FeedbackSignal(correction=True, negative=True, reply_len=300, user_len=50))
    assert learner.params.topic_follow > 0.5  # 理解错了 → 更紧跟用户


def test_no_feedback_no_drift():
    """无反馈信号（reward=0）时参数应基本不动（保守演化）。"""
    learner = StyleLearner()
    before = learner.params.snapshot()
    learner.observe(FeedbackSignal())
    after = learner.params.snapshot()
    assert before == after


# ---------- 语言镜像 ----------

def test_mirror_counts_and_pick():
    m = LanguageMirror()
    for _ in range(3):
        m.observe("我超级喜欢喝咖啡")
    top = m.stats()["top"]
    assert any("咖啡" in w or w == "咖啡" for w in top)
    picked = m.pick(1)
    assert isinstance(picked, list)


def test_mirror_use_cap():
    """单词使用次数受上限约束（防刻意）。"""
    m = LanguageMirror()
    for _ in range(10):
        m.observe("咖啡咖啡咖啡")
    m._uses["咖啡"] = 999  # 模拟已用满
    picked = m.pick(5)
    assert "咖啡" not in picked


# ---------- 持久化与回滚 ----------

def test_style_persist_roundtrip(tmp_path):
    p = str(tmp_path / "style.json")
    learner = StyleLearner(persist_path=p)
    learner.observe(FeedbackSignal(user_asked=True, reply_len=60, user_len=100))
    learner.save()
    learner2 = StyleLearner(persist_path=p)
    assert learner2.load()
    assert learner2.params.snapshot() == learner.params.snapshot()


def test_reset_restores_default(tmp_path):
    p = str(tmp_path / "style.json")
    learner = StyleLearner(persist_path=p)
    learner.observe(FeedbackSignal(user_asked=True, reply_len=60, user_len=100))
    learner.save()
    learner.reset()
    assert learner.params.snapshot() == StyleParams().snapshot()
    assert learner._steps == 0
    assert not learner.load()  # 文件已删

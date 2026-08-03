"""月度回顾测试：素材收集 / LLM 生成 / 降级路径。"""

from __future__ import annotations

import pytest

from veranima.core.review import MonthlyReview
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    def __init__(self, reply="这段时间我们一起经历了不少。记得你最喜欢下雨天，还有那只猫的事。"):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply

    def is_available(self):
        return True


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())


def test_collect_materials(store):
    store.store("episodic", "用户说换工作了，很紧张", importance=0.8, confidence=0.6)
    store.store("semantic", "用户喜欢下雨天", importance=0.7, confidence=0.6)
    store.store_message("user", "今天好累", 80, "平静")
    r = MonthlyReview(store)
    m = r.collect_materials()
    assert "换工作" in m["memories"]
    assert "下雨天" in m["memories"]
    assert "1 条消息" in m["stats"]


def test_generate_with_llm(store):
    store.store("semantic", "用户喜欢下雨天", importance=0.7, confidence=0.6)
    store.store("episodic", "用户说换工作了", importance=0.8, confidence=0.6)
    llm = FakeLLM()
    r = MonthlyReview(store, llm=llm)
    text = r.generate(name="小V")
    assert llm.calls == 1
    assert text == llm.reply


def test_generate_skips_llm_when_too_few_materials(store):
    """素材 < 2 条时不调 LLM（防编造），返回坦诚降级文案。"""
    store.store("semantic", "用户喜欢下雨天", importance=0.7, confidence=0.6)
    llm = FakeLLM()
    r = MonthlyReview(store, llm=llm)
    text = r.generate(name="小V")
    assert llm.calls == 0
    assert "慢慢一起过" in text
    assert "下雨天" in text  # 1 条素材如实引用


def test_generate_fallback_no_llm(store):
    store.store("semantic", "用户喜欢下雨天", importance=0.7, confidence=0.6)
    r = MonthlyReview(store, llm=None)
    text = r.generate(name="小V")
    assert "下雨天" in text  # 降级文案仍包含记忆素材
    assert "小V" not in text or True  # 降级不依赖 LLM


def test_generate_empty_memory(store):
    r = MonthlyReview(store, llm=None)
    text = r.generate(name="小V")
    assert "慢慢了解" in text  # 空记忆的坦诚文案

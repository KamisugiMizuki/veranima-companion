"""MVP2 curator 整理测试：去重 / 合并 / 低置信度丢弃 / 操作上限。

用字符袋假 embedding（相似文本 → 相似向量），验证相似度阈值逻辑。
"""

from __future__ import annotations

import pytest

from veranima.memory.store import MemoryStore


class BagEmbed:
    """字符袋 embedding：每字符一个桶（中文按字），相似文本向量相似。"""

    dim = 64

    def embed(self, texts):
        import hashlib
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                h = hashlib.md5(ch.encode()).digest()[0] % self.dim
                v[h] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=BagEmbed())


def test_curate_dedup_similar(store):
    """高度相似（≥0.92）→ 去重，保留新的。"""
    a = store.store("semantic", "我喜欢下雨天", importance=0.7, confidence=0.8)
    b = store.store("semantic", "我喜欢下雨天", importance=0.7, confidence=0.8)
    r = store.curate(sim_dup=0.9, sim_merge=0.7)
    assert r["ops"]["dedup"] >= 1
    assert len(store.list_layer("semantic")) == 1


def test_curate_merge_similar(store):
    """中等相似（≥merge，<dup）→ 合并版本链（M-1/MEMORY_SPEC 12.2：保留证据）。"""
    a = store.store("semantic", "我养了一只猫叫团子", importance=0.6, confidence=0.8)
    b = store.store("semantic", "我养了一只猫", importance=0.6, confidence=0.8)
    r = store.curate(sim_dup=0.99, sim_merge=0.6)
    assert r["ops"]["merge"] >= 1
    entries = store.list_layer("semantic")
    assert len(entries) == 2  # 合并新版本 + 原始证据 b（不再删除证据）
    assert any("团子" in e.content for e in entries)
    merged = max(entries, key=lambda e: e.version)
    assert merged.meta.get("supersedes") == b.id  # 版本链指向被合并的新条目（curate 取新在前）


def test_curate_drop_low_confidence(store):
    """低置信度 + 低强度 → 丢弃。"""
    store.store("semantic", "不重要的猜测", importance=0.3, confidence=0.3)
    r = store.curate()
    assert r["ops"]["drop"] >= 1
    assert len(store.list_layer("semantic")) == 0


def test_curate_keeps_high_confidence(store):
    """高置信度即使低强度也不丢。"""
    store.store("semantic", "重要的事", importance=0.3, confidence=0.9)
    r = store.curate()
    assert r["ops"]["drop"] == 0
    assert len(store.list_layer("semantic")) == 1


def test_curate_no_ops_when_empty(store):
    r = store.curate()
    assert r["ops"] == {"dedup": 0, "merge": 0, "drop": 0}

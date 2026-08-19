"""MEMORY_SPEC 12.2 curator 整理测试：过期清理 / 低置信标记 / 去重忽略 / 合并版本链 / 承诺到期。

用字符袋假 embedding（相似文本 → 相似向量），验证相似度阈值逻辑。
"""
from __future__ import annotations

import datetime

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
    """高度相似（≥0.92）→ 忽略重复，不删除任何一条（12.2 证据保留）。"""
    store.store("semantic", "我喜欢下雨天", importance=0.7, confidence=0.8)
    store.store("semantic", "我喜欢下雨天", importance=0.7, confidence=0.8)
    r = store.curate(sim_dup=0.9, sim_merge=0.7)
    assert r["ops"]["ignored"] >= 1
    assert len(store.list_layer("semantic")) == 2  # 两条都保留


def test_curate_merge_similar(store):
    """中等相似（≥merge，<dup）→ 合并版本链（保留证据）。"""
    a = store.store("semantic", "我养了一只猫叫团子", importance=0.6, confidence=0.8)
    b = store.store("semantic", "我养了一只猫", importance=0.6, confidence=0.8)
    r = store.curate(sim_dup=0.99, sim_merge=0.6)
    assert r["ops"]["versioned"] >= 1
    entries = store.list_layer("semantic")
    assert len(entries) == 2  # 合并新版本 + 原始证据 b
    assert any("团子" in e.content for e in entries)
    merged = max(entries, key=lambda e: e.version)
    assert merged.meta.get("supersedes") == b.id


def test_curate_marks_low_confidence(store):
    """低置信度 → 标记 low_confidence，不删除（12.2 证据保留）。"""
    store.store("semantic", "不重要的猜测", importance=0.3, confidence=0.3)
    r = store.curate()
    assert r["ops"]["ignored"] >= 1
    entries = store.list_layer("semantic")
    assert len(entries) == 1
    assert entries[0].meta.get("low_confidence") is True


def test_curate_keeps_high_confidence(store):
    """高置信度不标记。"""
    store.store("semantic", "重要的事", importance=0.3, confidence=0.9)
    r = store.curate()
    assert r["ops"]["ignored"] == 0
    assert len(store.list_layer("semantic")) == 1


def test_curate_expires_session(store):
    """过期 session → 清理。"""
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat(timespec="seconds")
    store.store("session", "过期任务", meta={"expires_at": past})
    r = store.curate()
    assert r["ops"]["expired"] >= 1
    assert store.list_layer("session") == []


def test_curate_expires_open_commitment(store):
    """open commitment 到期 → 版本链 status=expired。"""
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat(timespec="seconds")
    e = store.store("procedural", "承诺：周五交报告", meta={"promise": True, "status": "open", "expires_at": past})
    r = store.curate()
    assert r["ops"]["expired"] >= 1
    # 过期承诺不再出现在普通召回（默认过滤），链中可见终态
    current = [x for x in store.list_layer("procedural", include_superseded=True)
               if x.meta.get("status") == "expired"][0]
    assert current.status == "expired"


def test_curate_no_ops_when_empty(store):
    r = store.curate()
    assert r["ops"] == {"created": 0, "versioned": 0, "expired": 0, "ignored": 0, "conflict": 0}

"""记忆系统冒烟测试：store / recall / decay / erase / 版本链。

不依赖外部服务：使用临时数据库 + 确定性假 embedding provider。
运行：.venv/Scripts/python.exe -m pytest tests/ -v
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from veranima.memory.store import MemoryStore


class FakeEmbed:
    """确定性假 embedding：基于文本哈希生成 8 维向量。"""

    dim = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [b / 255.0 for b in h[:8]]
            out.append(vec)
        return out


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(
        db_path=str(tmp_path / "test.db"),
        config={"decay_enabled": True, "importance_base_s": 3600},
        provider=FakeEmbed(),
    )
    yield s
    s.con.close()  # 释放 WAL 句柄，避免 Windows 删除临时目录失败


def test_store_and_get(store):
    e = store.store("semantic", "用户喜欢蓝色", importance=0.8)
    assert e.id > 0
    got = store.get(e.id)
    assert got.content == "用户喜欢蓝色"
    assert got.layer == "semantic"
    assert got.strength == 1.0
    assert got.version == 1


def test_store_invalid_layer(store):
    with pytest.raises(ValueError):
        store.store("nope", "x")


def test_message_ingestion_and_fts(store):
    mid = store.store_message("user", "我今天很开心，项目汇报成功了")
    assert mid > 0
    msgs = store.recent_messages(limit=5)
    assert any(m["id"] == mid for m in msgs)
    # FTS5 命中（trigram 分词，查询需 ≥3 字符）
    rows = store.con.execute(
        "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?", ('"汇报成功"',)
    ).fetchall()
    assert any(r["rowid"] == mid for r in rows)


def test_message_created_at_lookup(store):
    mid = store.store_message("user", "带时间的消息")
    stamp = store.message_created_at(mid)
    assert stamp and "T" in stamp
    assert store.message_created_at(999999) is None


def test_messages_keep_channel_and_can_filter(store):
    store.store_message("user", "QQ 消息", channel="qq")
    store.store_message("user", "桌宠消息", channel="pet")

    rows = store.recent_messages(limit=10, channel="qq")

    assert [row["content"] for row in rows] == ["QQ 消息"]
    assert rows[0]["channel"] == "qq"


def test_proactive_feedback_keeps_channel_and_candidate(store):
    store.record_proactive_feedback(
        source="shared_episode", channel="qq", candidate_id="qq-1"
    )

    row = store.recent_proactive_feedback(channel="qq", limit=1)[0]

    assert row["channel"] == "qq"
    assert row["candidate_id"] == "qq-1"


def test_proactive_feedback_keeps_reply_expectation(store):
    store.record_proactive_feedback(
        source="scene", channel="qq", candidate_id="c1",
        requires_reply=True, direct_question="后来怎么样？",
        expires_at="2026-08-23T00:00:00+00:00",
    )

    row = store.recent_proactive_feedback(channel="qq", limit=1)[0]

    assert row["requires_reply"] == 1
    assert row["direct_question"] == "后来怎么样？"
    assert row["expires_at"] == "2026-08-23T00:00:00+00:00"
    assert row["candidate_id"] == "c1"
    assert row["expectation_status"] == "pending"

    assert store.expire_proactive_expectation(row["id"]) is True
    assert store.expire_proactive_expectation(row["id"]) is False
    assert store.recent_proactive_feedback(channel="qq", limit=1)[0]["expectation_status"] == "expired"

    store.record_proactive_feedback(source="scene", channel="qq", responded=True)
    assert store.recent_proactive_feedback(channel="qq", limit=1)[0]["expectation_status"] == "expired"


def test_recall_by_similarity(store):
    store.store("episodic", "用户上个月通过了考试", importance=0.9)
    store.store("episodic", "用户最近在备考心理学", importance=0.7)
    store.store("episodic", "无关内容：今天天气不错", importance=0.3)
    hits = store.recall("考试考得怎么样", top_k=3)
    assert len(hits) >= 1
    assert "考试" in hits[0].content


def test_recall_layer_filter(store):
    store.store("semantic", "用户讨厌香菜", importance=0.6)
    store.store("episodic", "香菜事件", importance=0.6)
    hits = store.recall("香菜", top_k=5, layer="semantic")
    assert all(h.layer == "semantic" for h in hits)
    assert any("讨厌香菜" in h.content for h in hits)


def test_version_chain(store):
    e1 = store.store("semantic", "用户喜欢蓝色", importance=0.5)
    e2 = store.update_latest(e1.id, "用户现在喜欢红色", confidence=1.0)
    assert e2.version == 2
    # 旧版本保留
    old = store.get(e1.id)
    assert old.content == "用户喜欢蓝色"
    assert store.get(e2.id).content == "用户现在喜欢红色"


def test_erase_by_keyword(store):
    store.store("semantic", "用户的生日是3月15日", importance=0.9)
    store.store("episodic", "用户提到了生日聚会", importance=0.5)
    store.store("semantic", "用户喜欢蓝色", importance=0.5)
    n = store.erase(content_contains="生日")
    assert n == 2
    remaining = store.list_layer("semantic") + store.list_layer("episodic")
    assert all("生日" not in e.content for e in remaining)


def test_erase_by_id_cascades(store):
    e = store.store("semantic", "要删除的记忆", importance=0.5)
    assert store.erase(memory_id=e.id) == 1
    assert store.get(e.id) is None


def test_decay_reduces_strength(store):
    e = store.store("semantic", "旧记忆", importance=0.2)  # 低重要性 → 衰减快
    # 手动把 updated_at 改为 2 小时前
    import datetime
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat(timespec="seconds")
    store.con.execute("UPDATE memories SET updated_at=? WHERE id=?", (old, e.id))
    store.con.commit()
    result = store.decay()
    assert result["updated"] >= 1
    assert store.get(e.id).strength < 1.0
    # 高重要性记忆衰减更慢
    e2 = store.store("semantic", "重要记忆", importance=0.95)
    store.con.execute("UPDATE memories SET updated_at=? WHERE id=?", (old, e2.id))
    store.con.commit()
    store.decay()
    assert store.get(e2.id).strength > store.get(e.id).strength


def test_curate_returns_counts(store):
    store.store("procedural", "用户要求：不要打断我说话", importance=0.9)
    stats = store.curate()
    assert stats["counts"]["procedural"] >= 1

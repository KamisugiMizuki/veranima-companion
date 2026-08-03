"""L2 → cosine 迁移测试：旧库升级后数据保留、向量重建、检索可用。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def _make_old_l2_db(path: str):
    """构造旧版数据库：memory_vec 为默认 L2 度量 + 一条记忆。"""
    con = sqlite3.connect(path)
    con.enable_load_extension(True)
    try:
        import sqlite_vec
        sqlite_vec.load(con)
    except Exception:
        pass
    con.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            layer TEXT NOT NULL, content TEXT NOT NULL, importance REAL,
            confidence REAL, provenance TEXT, version INTEGER, strength REAL,
            category TEXT, meta TEXT, created_at TEXT, updated_at TEXT,
            last_access_at TEXT)"""
    )
    con.execute(
        "CREATE VIRTUAL TABLE memory_vec USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[8])"
    )
    con.execute(
        "INSERT INTO memories(layer, content, importance, confidence, provenance, version, strength, category, meta, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("semantic", "我特别喜欢下雨天", 0.7, 0.6, "auto-extract", 1, 1.0, "preference", "{}", "t", "t"),
    )
    con.commit()
    con.close()


def test_migration_l2_to_cosine(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_l2_db(db)
    # 新代码打开旧库：应重建 cosine 并重新嵌入旧记忆
    m = MemoryStore(db_path=db, config={}, provider=FakeEmbed())
    sql = m.con.execute("SELECT sql FROM sqlite_master WHERE name='memory_vec'").fetchone()[0]
    assert "distance_metric=cosine" in sql
    # 数据保留
    rows = m.con.execute("SELECT * FROM memories").fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "我特别喜欢下雨天"
    # 向量重建
    n_vec = m.con.execute("SELECT count(*) FROM memory_vec").fetchone()[0]
    assert n_vec == 1
    # 检索可用
    rec = m.recall("我喜欢的天气", top_k=3, layer="semantic")
    assert len(rec) >= 1


def test_no_migration_on_fresh_db(tmp_path):
    """新库直接是 cosine，不触发迁移。"""
    m = MemoryStore(db_path=str(tmp_path / "new.db"), config={}, provider=FakeEmbed())
    sql = m.con.execute("SELECT sql FROM sqlite_master WHERE name='memory_vec'").fetchone()[0]
    assert "distance_metric=cosine" in sql

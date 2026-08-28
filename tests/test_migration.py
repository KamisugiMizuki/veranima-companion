"""vec0 老库 → memory_embedding 迁移测试：数据保留、向量重存、检索可用。"""

from __future__ import annotations

import json
import sqlite3
from array import array

import pytest

from veranima.memory.schema import init_db, unit_blob
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def _make_old_vec0_db(path: str):
    """构造旧版数据库：vec0 cosine 虚拟表 + 一条记忆 + 其向量（json 文本，旧格式）。"""
    sqlite_vec = pytest.importorskip("sqlite_vec")
    con = sqlite3.connect(path)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
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
        "INSERT INTO memories(layer, content, importance, confidence, provenance, version,"
        " strength, category, meta, created_at, updated_at)"
        " VALUES ('semantic','我特别喜欢下雨天',0.7,0.6,'auto-extract',1,1.0,'preference','{}','t','t')",
    )
    vec = FakeEmbed().embed(["我特别喜欢下雨天"])[0]
    con.execute("INSERT INTO memory_vec(memory_id, embedding) VALUES (?,?)", (1, json.dumps(vec)))
    con.commit()
    con.close()


def test_vec0_migrated_to_blob_table(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_vec0_db(db)
    m = MemoryStore(db_path=db, config={}, provider=FakeEmbed())
    # vec0 表已消失，blob 表接管
    names = {r[0] for r in m.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_embedding" in names and "memory_vec" not in names
    # 数据保留
    rows = m.con.execute("SELECT * FROM memories").fetchall()
    assert len(rows) == 1 and rows[0]["content"] == "我特别喜欢下雨天"
    # 向量重存为归一化 blob（8 维 float32 = 32 字节）
    r = m.con.execute("SELECT embedding FROM memory_embedding WHERE memory_id=1").fetchone()
    assert r and len(bytes(r[0])) == 32
    # 检索可用（走 blob KNN 路径）
    rec = m.recall("我喜欢的天气", top_k=3, layer="semantic")
    assert len(rec) >= 1 and rec[0].content == "我特别喜欢下雨天"


def test_fresh_db_no_migration_no_provider_needed(tmp_path):
    """新库直接是 blob 表；不碰 sqlite-vec（安卓路径的行为等价面）。"""
    m = MemoryStore(db_path=str(tmp_path / "new.db"), config={}, provider=FakeEmbed())
    names = {r[0] for r in m.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_embedding" in names and "memory_vec" not in names


def test_stale_dim_vectors_skipped_not_crash(tmp_path):
    """换模型后残留的陈旧维度向量：recall 跳过不炸（重铸由备份导入负责）。"""
    db = str(tmp_path / "s.db")
    m = MemoryStore(db_path=db, config={}, provider=FakeEmbed())
    m.store("semantic", "新记忆内容")
    m.con.execute("INSERT INTO memory_embedding(memory_id, embedding) VALUES (999,?)",
                  (b"\x00" * 24,))  # 6 维垃圾向量
    m.con.commit()
    rec = m.recall("新记忆", top_k=5)
    assert rec and all(e.id != 999 for e in rec)


def test_unit_blob_is_normalized():
    import math
    b = unit_blob([3.0, 4.0])
    v = array("f"); v.frombytes(b)
    assert len(v) == 2
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-6

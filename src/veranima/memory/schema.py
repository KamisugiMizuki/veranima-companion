"""记忆系统 SQLite schema：五层记忆 + 消息（零开销摄入）+ FTS5 + 向量表。"""

import sqlite3
from pathlib import Path

# 五层记忆（DESIGN.md 3 节）
LAYERS = ("core_profile", "semantic", "episodic", "procedural", "session")

# bge-m3 embedding 维度（换 embedding 模型时同步改；运行时以 provider.dim 为准）
EMBEDDING_DIM = 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    layer         TEXT NOT NULL CHECK (layer IN ('core_profile','semantic','episodic','procedural','session')),
    content       TEXT NOT NULL,
    importance    REAL NOT NULL DEFAULT 0.5,
    confidence    REAL NOT NULL DEFAULT 0.75,
    provenance    TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    strength      REAL NOT NULL DEFAULT 1.0,
    category      TEXT,
    meta          TEXT,
    last_access_at TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_layer    ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_strength ON memories(strength);
CREATE INDEX IF NOT EXISTS idx_memories_created  ON memories(created_at);

-- 原始消息（零开销摄入：写入即存，不等待 LLM）
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    energy_at  REAL,
    mood_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- FTS5 全文索引（BM25 检索，trigram 分词以支持中文），触发器自动同步
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

-- 向量表（sqlite-vec）
CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
    memory_id INTEGER PRIMARY KEY,
    embedding float[{dim}]
);
"""


def init_db(db_path: str | Path, dim: int = EMBEDDING_DIM) -> sqlite3.Connection:
    """初始化数据库并返回连接（启用外键/扩展加载）。dim 为向量维度。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    # 加载 sqlite-vec 扩展
    con.enable_load_extension(True)
    try:
        import sqlite_vec
        sqlite_vec.load(con)
    except Exception:
        # 扩展不可用时向量检索降级（FTS5 仍可用）
        pass
    con.executescript(SCHEMA.format(dim=dim))
    con.commit()
    return con

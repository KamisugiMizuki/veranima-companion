"""记忆系统 SQLite schema：五层记忆 + 消息（零开销摄入）+ FTS5 + 向量表。"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

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

-- R4 主动消息反馈（R4_SPEC 4：忽略与自愈）
CREATE TABLE IF NOT EXISTS proactive_feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at          TEXT NOT NULL,
    source           TEXT NOT NULL,
    responded        INTEGER NOT NULL DEFAULT 0,
    interrupted      INTEGER NOT NULL DEFAULT 0,
    user_sent_within INTEGER,              -- 秒；主动后用户多久来消息（0=无）
    dismissed        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_proactive_feedback_source ON proactive_feedback(source);

-- Agent 内在状态（依恋度/精力/情绪/计数），单行，跨重启持久化（2026-08-04 续接）
CREATE TABLE IF NOT EXISTS agent_state (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    energy         REAL NOT NULL DEFAULT 100.0,
    mood           TEXT NOT NULL DEFAULT '平静',
    attachment     REAL NOT NULL DEFAULT 0.5,
    mood_score     REAL NOT NULL DEFAULT 0.0,
    total_messages INTEGER NOT NULL DEFAULT 0,
    -- R1 状态契约（R1_SPEC 5）：旧库由 init_db 迁移补列
    social_appetite        REAL NOT NULL DEFAULT 0.8,
    attention_topic        TEXT NOT NULL DEFAULT '',
    attention_scene        TEXT NOT NULL DEFAULT 'normal',
    last_interaction_channel TEXT NOT NULL DEFAULT '',
    last_cause             TEXT NOT NULL DEFAULT 'startup',
    updated_at     TEXT NOT NULL
);

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

-- M-3 规范记忆 FTS（MEMORY_SPEC 10.2：直接索引 memories）
-- 独立 fts5 表（非外部内容表）：由 store.py 显式同步（store/update_latest/erase），
-- 外部内容表+触发器模式在迁移/删除路径会损坏库（实测 malformed），不采用
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tokenize='trigram'
);

-- 记忆向量（cosine 度量：distance = 1 - 余弦相似度，recall 直接 1-distance 即相似度）
CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
    memory_id INTEGER PRIMARY KEY,
    embedding float[{dim}] distance_metric=cosine
);
"""


def init_db(db_path: str | Path, dim: int = EMBEDDING_DIM, provider=None) -> sqlite3.Connection:
    """初始化数据库并返回连接（启用外键/扩展加载）。dim 为向量维度。

    provider 用于迁移时重新嵌入旧记忆（旧库 L2 → cosine 重建后补向量）。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：QQ 形态下 agent.handle 在 to_thread 工作线程执行，
    # 后台主动线程也会访问连接；连接自带内部锁，配合 WAL + busy_timeout 串行安全
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
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
    # M-3 迁移：memories_fts 已建但旧记忆行未索引 → 全量重建（独立表，普通 DELETE/INSERT 安全）
    try:
        fts_count = con.execute("SELECT count(*) FROM memories_fts").fetchone()[0]
        mem_count = con.execute("SELECT count(*) FROM memories").fetchone()[0]
        if fts_count != mem_count:
            con.execute("DELETE FROM memories_fts")
            rows = con.execute("SELECT id, content FROM memories WHERE content != ''").fetchall()
            con.executemany(
                "INSERT INTO memories_fts(rowid, content) VALUES (?,?)",
                [(r["id"], r["content"]) for r in rows],
            )
            con.commit()
            logger.info("memories_fts rebuilt for %s memories", mem_count)
    except Exception as e:
        logger.warning("memories_fts migration check failed: %s", e)
    # 迁移：旧版 memory_vec 为默认 L2 度量（distance≠余弦），重建为 cosine 并重新嵌入
    try:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name='memory_vec'").fetchone()[0]
        if "distance_metric=cosine" not in sql:
            con.execute("DROP TABLE memory_vec")
            con.executescript(SCHEMA.format(dim=dim))
            logger.warning("memory_vec rebuilt with cosine metric (old L2 dropped)")
            if provider is not None:
                rows = con.execute("SELECT id, content FROM memories WHERE content != ''").fetchall()
                import json
                reembedded = 0
                for r in rows:
                    try:
                        vec = provider.embed([r["content"]])[0]
                        con.execute(
                            "INSERT INTO memory_vec(memory_id, embedding) VALUES (?,?)",
                            (r["id"], json.dumps(vec)),
                        )
                        reembedded += 1
                    except Exception:
                        continue
                con.commit()
                logger.info("re-embedded %s memories after migration", reembedded)
    except Exception as e:
        logger.warning("memory_vec migration check failed: %s", e)
    # 迁移：agent_state 补 R1 列（R1_SPEC 5，旧库无新列）
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(agent_state)").fetchall()}
        for name, ddl in (
            ("social_appetite", "REAL NOT NULL DEFAULT 0.8"),
            ("attention_topic", "TEXT NOT NULL DEFAULT ''"),
            ("attention_scene", "TEXT NOT NULL DEFAULT 'normal'"),
            ("last_interaction_channel", "TEXT NOT NULL DEFAULT ''"),
            ("last_cause", "TEXT NOT NULL DEFAULT 'startup'"),
            # P-3（PERSONA_LOOP_SPEC）：PAD + 关系快照
            ("valence", "REAL NOT NULL DEFAULT 0.5"),
            ("arousal", "REAL NOT NULL DEFAULT 0.5"),
            ("dominance", "REAL NOT NULL DEFAULT 0.5"),
            ("relationship", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE agent_state ADD COLUMN {name} {ddl}")
                logger.info("agent_state migration: added column %s", name)
    except Exception as e:
        logger.warning("agent_state migration check failed: %s", e)
    con.commit()
    return con

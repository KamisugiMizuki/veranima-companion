"""记忆系统 SQLite schema：五层记忆 + 消息（零开销摄入）+ FTS5 + 向量表。"""

import logging
import math
import sqlite3
from array import array
from pathlib import Path
from typing import Sequence

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

-- MEMORY_BACKEND_EVAL M-D 审核准入收件箱（默认关闭；批准后才写入规范记忆）
CREATE TABLE IF NOT EXISTS memory_review_inbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cand_json   TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);

-- HERMES_AGENT_INTEGRATION_SPEC：task_id <-> run_id 执行审计（不属于五层人物记忆）
CREATE TABLE IF NOT EXISTS task_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL UNIQUE,
    run_id        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'queued',
    raw_status    TEXT NOT NULL DEFAULT '',
    output        TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    report_json   TEXT NOT NULL DEFAULT '{{}}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- 原始消息（零开销摄入：写入即存，不等待 LLM）
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content    TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'qq',
    created_at TEXT NOT NULL,
    energy_at  REAL,
    mood_at    TEXT,
    tone_at    TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '',
    role_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS sleep_message_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    user_scope TEXT NOT NULL,
    sleep_cycle_id TEXT NOT NULL,
    message_id INTEGER,
    received_at TEXT NOT NULL,
    sender_scope TEXT NOT NULL,
    content_retained INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT,
    UNIQUE(role_id, sleep_cycle_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_sleep_archive_cycle ON sleep_message_archive(role_id, user_scope, sleep_cycle_id);

-- 用户睡眠周期（2026-08-30 用户拍板）：用户明确报告入睡/苏醒时记录时间点。
-- 每次长睡眠苏醒后 LLM 生成睡眠状况总结（角色口吻）。
CREATE TABLE IF NOT EXISTS sleep_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fell_asleep_at TEXT NOT NULL,          -- 入睡时刻（UTC ISO）
    woke_at TEXT,                          -- 苏醒时刻（UTC ISO，未苏醒=NULL）
    source TEXT NOT NULL DEFAULT 'report', -- 报告来源（report=用户明说）
    summary TEXT NOT NULL DEFAULT '',      -- 苏醒后 LLM 生成的总结（角色口吻）
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sleep_cycles_fell ON sleep_cycles(fell_asleep_at DESC);

CREATE TABLE IF NOT EXISTS virtual_life_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    plan_id TEXT NOT NULL DEFAULT '',
    item_id TEXT,
    event_kind TEXT NOT NULL,
    truth_class TEXT NOT NULL DEFAULT 'virtual_simulation',
    summary TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{{}}',
    cycle_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_virtual_life_role ON virtual_life_events(role_id, id DESC);

CREATE TABLE IF NOT EXISTS user_info_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    user_scope TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    source_message_id INTEGER NOT NULL,
    last_asked_at TEXT,
    ask_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(role_id, user_scope, topic_key)
);

-- SelfModel 人生章节（独立于原始消息和规范记忆；每章可审计、可更新）
CREATE TABLE IF NOT EXISTS self_model_chapters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    period_start        TEXT,
    period_end          TEXT,
    key_events          TEXT NOT NULL DEFAULT '[]',
    self_interpretation TEXT NOT NULL DEFAULT '',
    relationship_changes TEXT NOT NULL DEFAULT '[]',
    open_threads        TEXT NOT NULL DEFAULT '[]',
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_self_model_chapters_updated ON self_model_chapters(updated_at);

-- R4 主动消息反馈（R4_SPEC 4：忽略与自愈）
CREATE TABLE IF NOT EXISTS proactive_feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at          TEXT NOT NULL,
    source           TEXT NOT NULL,
    channel          TEXT NOT NULL DEFAULT 'qq',
    candidate_id     TEXT NOT NULL DEFAULT '',
    requires_reply   INTEGER NOT NULL DEFAULT 0,
    direct_question TEXT NOT NULL DEFAULT '',
    expires_at      TEXT,
    expectation_status TEXT NOT NULL DEFAULT 'none',
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

-- 用户画像（2026-09-01「异地恋人」设计稿裁决）：角色无关的「用户是谁」，
-- 切角色不重置；结构化字段走本表，自由文本细节仍走 memories(user_fact)。
-- 称呼按角色隔离 → user_nicknames（角色×用户对）。
CREATE TABLE IF NOT EXISTS user_profile (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',   -- user=用户自述 / dialog=对话提取 / system=系统统计
    confidence  REAL NOT NULL DEFAULT 1.0,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_nicknames (
    role_id     TEXT NOT NULL,
    nickname    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'current',  -- current / forbidden / history
    stage       TEXT NOT NULL DEFAULT '',         -- 记录使用时的关系阶段
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (role_id, nickname)
);

-- 多角色未读角标：last_read_id=已读指针（按消息自增 id 计，开会话页=追平）
CREATE TABLE IF NOT EXISTS role_reads (
    role_id      TEXT PRIMARY KEY,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

-- 牵挂账本（MIND_LOOP_SPEC M1）：角色心里持续挂着的事，tick 程序驱动演进
CREATE TABLE IF NOT EXISTS mind_threads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id      TEXT NOT NULL,
    topic        TEXT NOT NULL,
    origin       TEXT NOT NULL CHECK (origin IN ('schedule','user','promise','thread','self')),
    intensity    REAL NOT NULL DEFAULT 0.6,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
    beat_step    INTEGER NOT NULL DEFAULT 0,
    next_beat_at TEXT NOT NULL DEFAULT '',
    last_spoken_at TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_threads_role ON mind_threads(role_id, status);

-- 好友动态（MOMENTS_MULTIROLE_SPEC P2）：动态=角色虚拟生活的自然溢出，
-- 每条带 kind（D01-D07 溯源类型）与 dedupe_key（素材唯一键，重复生成被
-- UNIQUE 静默拒绝=天然去重）；role_id='user'=用户动态（P4 才产生）。
CREATE TABLE IF NOT EXISTS moments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'D05',
    source_ref  TEXT NOT NULL DEFAULT '',
    dedupe_key  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moments_role ON moments(role_id, id);

-- 赞/评流水（actor='user' 或角色目录名）。like 幂等在代码层查存在性，
-- comment/reply 多条天然允许。
CREATE TABLE IF NOT EXISTS moment_interactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    moment_id  INTEGER NOT NULL,
    actor      TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('like','comment','reply','seen')),
    content    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mi_moment ON moment_interactions(moment_id);

-- 角色独立设置（role_settings）：JSON 一列走天下（moments/proactive/互动三组，
-- 默认值在 core 侧 merge，不在库里铺列）。
CREATE TABLE IF NOT EXISTS role_settings (
    role_id    TEXT PRIMARY KEY,
    config     TEXT NOT NULL DEFAULT '{{}}',
    updated_at TEXT NOT NULL
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

-- 记忆向量（方案 B，2026-08 定案）：归一化 float32 blob + Python 暴力点积。
-- sqlite-vec 本来就是 brute-force exact KNN（无 ANN），本表与其结果逐位一致；
-- 换宿主换出的是 Chaquopy 兼容性（安卓系统 libsqlite3 无 enable_load_extension）。
CREATE TABLE IF NOT EXISTS memory_embedding (
    memory_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL
);

-- 决策留痕（HARNESS_SPEC D1）：每次自发副作用（主动消息/苏醒播报/动态发布）
-- 落一行——导出即账本，「她为什么说了/没说这句」不再靠 DB 考古猜。
-- 只记自发行为；对话一问一答不进（量大且非验收面）。
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    role_id     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL,               -- wakesummary/meal/thread/greeting/ritual/heartbeat/moment…
    object_ref  TEXT NOT NULL DEFAULT '',    -- sleep_cycle:7 / moment:41（被操作对象指针）
    verdict     TEXT NOT NULL,               -- sent/rejected/expired/deduped/failed
    reason      TEXT NOT NULL DEFAULT '',    -- 判定短句（人读）
    digest      TEXT NOT NULL DEFAULT '',    -- 决策时刻读到的关键输入摘要（≤200 字）
    effect_ref  INTEGER NOT NULL DEFAULT 0   -- 产出去向（message/moment id；0=无产出）
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(id);
"""


def unit_blob(vec: Sequence[float]) -> bytes:
    """L2 归一化 → float32 blob（cosine 退化为点积，读取端零归一化成本）。"""
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array("f", (x / n for x in vec)).tobytes()


def _drop_vec0_shadows(con: sqlite3.Connection) -> None:
    """清 sqlite-vec 的 vec0 影子表（rowids/chunks）。

    主表 memory_vec 被 DROP 后影子表仍会单独残留（实机 177 行 rowids + 1 行 chunks），
    它们是 vec0 的内部索引，脱离 memory_vec 后毫无用处、只占体积。
    """
    for shadow in ("memory_vec_rowids", "memory_vec_chunks"):
        try:
            con.execute(f"DROP TABLE IF EXISTS {shadow}")
        except Exception as e:
            logger.debug("drop %s failed: %s", shadow, e)


def migrate_vec0(con: sqlite3.Connection) -> int:
    """一次性迁移：老库的 vec0 虚拟表 → memory_embedding（读原始 blob 归一化重存）。

    依赖 sqlite-vec 扩展（PC 端有、安卓端老库根本建不出 vec0 表——不存在即返回 0）。
    扩展不可用但 vec0 表存在时跳过：备份导入路径会全量重铸，不静默丢数据。
    """
    if not con.execute("SELECT count(*) FROM sqlite_master WHERE name='memory_vec'").fetchone()[0]:
        # 主表不在也可能是「迁移已完成、影子表残留」——顺手清一次
        _drop_vec0_shadows(con)
        con.commit()
        return 0
    try:
        con.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(con)
        rows = con.execute("SELECT memory_id, embedding FROM memory_vec").fetchall()
    except Exception as e:
        logger.warning("vec0 migration skipped (%s); embeddings re-forge via backup import", e)
        return 0
    copied = 0
    for r in rows:
        v = array("f")
        v.frombytes(bytes(r["embedding"]))
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        con.execute(
            "INSERT OR REPLACE INTO memory_embedding(memory_id, embedding) VALUES (?,?)",
            (r["memory_id"], array("f", (x / n for x in v)).tobytes()),
        )
        copied += 1
    con.execute("DROP TABLE memory_vec")
    _drop_vec0_shadows(con)
    con.commit()
    logger.info("migrated %d vec0 embeddings to memory_embedding", copied)
    return copied


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
    con.executescript(SCHEMA.format(dim=dim))
    # 老库 vec0 虚拟表一次性迁移（新库无此表，直接零成本通过）
    try:
        migrate_vec0(con)
    except Exception as e:
        logger.warning("vec0 migration check failed: %s", e)
    # 虚拟生活事件迁移：早期实现没有 cycle_key；先补列再创建唯一索引。
    try:
        life_cols = {r["name"] for r in con.execute("PRAGMA table_info(virtual_life_events)").fetchall()}
        if "cycle_key" not in life_cols:
            con.execute("ALTER TABLE virtual_life_events ADD COLUMN cycle_key TEXT NOT NULL DEFAULT ''")
        con.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_life_cycle
               ON virtual_life_events(role_id, event_kind, cycle_key) WHERE cycle_key != ''"""
        )
    except Exception as e:
        logger.warning("virtual_life_events migration check failed: %s", e)
    # 牵挂发送窗口落库（09-08：原 _last_material 是进程内存态，安卓端进程被
    # 系统回收即丢——同一条牵挂 12h 内裸发 5 次）。
    try:
        thread_cols = {r["name"] for r in con.execute("PRAGMA table_info(mind_threads)").fetchall()}
        if "last_spoken_at" not in thread_cols:
            con.execute("ALTER TABLE mind_threads ADD COLUMN last_spoken_at TEXT NOT NULL DEFAULT ''")
    except Exception as e:
        logger.warning("mind_threads migration check failed: %s", e)
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
    # 迁移：sleep_cycles 补 claimed 列（苏醒总结认领位，09-06 复读实锤根修）。
    # 旧列：去重记在共享单行表 proactive_feedback（无角色归属、多消费方都往里
    # 记同名键、且用户回一句就被 responded 记账污染）——认领状态跟着周期走才成立
    try:
        scols = {r["name"] for r in con.execute("PRAGMA table_info(sleep_cycles)").fetchall()}
        if "claimed" not in scols:
            con.execute("ALTER TABLE sleep_cycles ADD COLUMN claimed INTEGER NOT NULL DEFAULT 0")
            # 存量带总结的周期=旧版旁路已播报过（真机 09-05/09-06 复读的那批）——
            # 全部置已认领，升级后任何一次旁路轮询都不可能再重放旧总结
            con.execute("UPDATE sleep_cycles SET claimed=1 WHERE summary<>''")
            logger.info("sleep_cycles migration: added column claimed (backfilled sent)")
        con.commit()
    except Exception as e:
        logger.warning("sleep_cycles claimed migration failed: %s", e)
    # 迁移：messages 补 role_id 列（多角色会话隔离，旧库无新列；存量行留
    # ''，由 app boot 时回填为当时的活跃角色——store.py 不认识"凛"这种业务值）
    try:
        mcols = {r["name"] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
        if "role_id" not in mcols:
            con.execute("ALTER TABLE messages ADD COLUMN role_id TEXT NOT NULL DEFAULT ''")
            logger.info("messages migration: added column role_id")
        con.execute("CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role_id, id)")
        con.commit()
    except Exception as e:
        logger.warning("messages role_id migration failed: %s", e)
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
            # 用户睡眠周期（2026-08-30 用户拍板）：user_asleep=用户当前是否在睡
            ("user_asleep", "INTEGER NOT NULL DEFAULT 0"),
            ("last_sleep_report_at", "TEXT NOT NULL DEFAULT ''"),
            # 出现=醒来（2026-09-11 用户拍板）：活动信号维护的「最后活动集群起点」
            # （滚动）+ 最近信号时刻；报告到达时取两者定案。共享用户态家族，只走列级写
            ("inferred_woke_at", "TEXT NOT NULL DEFAULT ''"),
            ("last_signal_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE agent_state ADD COLUMN {name} {ddl}")
                logger.info("agent_state migration: added column %s", name)
    except Exception as e:
        logger.warning("agent_state migration check failed: %s", e)
    # R4/QQ proactive 迁移：旧反馈默认归 QQ，新记录按通道隔离。
    try:
        feedback_cols = {r["name"] for r in con.execute("PRAGMA table_info(proactive_feedback)").fetchall()}
        for name, ddl in (
            ("channel", "TEXT NOT NULL DEFAULT 'qq'"),
            ("candidate_id", "TEXT NOT NULL DEFAULT ''"),
            ("requires_reply", "INTEGER NOT NULL DEFAULT 0"),
            ("direct_question", "TEXT NOT NULL DEFAULT ''"),
            ("expires_at", "TEXT"),
            ("expectation_status", "TEXT NOT NULL DEFAULT 'none'"),
            ("followup_status", "TEXT"),  # 追问闭环：NULL/''=未追问, 'asked'=已追问一次
        ):
            if name not in feedback_cols:
                con.execute(f"ALTER TABLE proactive_feedback ADD COLUMN {name} {ddl}")
                logger.info("proactive_feedback migration: added column %s", name)
        con.execute("CREATE INDEX IF NOT EXISTS idx_proactive_feedback_channel ON proactive_feedback(channel)")
    except Exception as e:
        logger.warning("proactive_feedback migration check failed: %s", e)
    try:
        message_cols = {r["name"] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
        if "channel" not in message_cols:
            con.execute("ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL DEFAULT 'qq'")
            logger.info("messages migration: added channel")
        if "attachments" not in message_cols:
            con.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT ''")
            logger.info("messages migration: added attachments")
        if "tone_at" not in message_cols:
            con.execute("ALTER TABLE messages ADD COLUMN tone_at TEXT NOT NULL DEFAULT ''")
            logger.info("messages migration: added tone_at")
    except Exception as e:
        logger.warning("messages channel migration check failed: %s", e)
    con.commit()
    return con

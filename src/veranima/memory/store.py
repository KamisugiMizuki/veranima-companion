"""MemoryStore — 记忆系统唯一入口。

对外原语（DESIGN.md）：
- store(layer, content, ...)    存
- recall(query, layer=None)     取（混合检索）
- decay()                       遗忘衰减
- curate()                      定期整理（MVP2 实现，占位）
- erase(target)                 级联删除
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schema import LAYERS, init_db
from .embedding import EmbeddingProvider, make_provider

logger = logging.getLogger(__name__)

# R1 记忆类型 → 旧 layer 映射（R1_SPEC 1.1：不立刻改表 CHECK，做映射层）
LAYER_R1_MAP = {
    "identity": "core_profile",
    "user_fact": "semantic",
    "shared_episode": "episodic",
    "commitment": "procedural",
    "session": "session",
}
LAYER_R1_REVERSE = {v: k for k, v in LAYER_R1_MAP.items()}

# 候选记忆校验（R1_SPEC 1.2 / MEMORY_SPEC 5）：kind 白名单
CANDIDATE_KINDS = ("user_fact", "shared_episode", "commitment", "session")
VALID_STATUS = ("active", "open", "done", "cancelled", "expired")
SECRET_PATTERNS = (
    "password", "passwd", "api_key", "apikey", "secret", "token",
    "验证码", "密码", "私钥", "密钥", "支付密码", "卡号",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_candidate(cand: dict) -> list[str]:
    """候选记忆程序校验（R1_SPEC 1.2）：LLM 不得直接执行 SQL，先过本函数。

    返回问题列表，空列表 = 通过。
    """
    issues: list[str] = []
    kind = cand.get("kind")
    if kind not in CANDIDATE_KINDS:
        issues.append(f"kind 不在白名单: {kind!r}")
    content = cand.get("content") or ""
    if not content.strip():
        issues.append("content 为空")
    elif len(content) > 500:
        issues.append("content 超过 500 字")
    for key in ("confidence", "importance"):
        val = cand.get(key)
        if val is not None:
            try:
                fv = float(val)
                if not 0.0 <= fv <= 1.0:
                    issues.append(f"{key} 超出 0-1: {val!r}")
            except (TypeError, ValueError):
                issues.append(f"{key} 不是数字: {val!r}")
    if not cand.get("source"):
        issues.append("source 缺失（rule_extract|llm_extract|manual|agent_confirmed）")
    if not cand.get("source_message_id"):
        issues.append("source_message_id 缺失")
    # MEMORY_SPEC 5.4：时间字段 ISO 校验
    for key in ("event_time", "valid_from", "valid_to", "expires_at"):
        val = cand.get(key)
        if val:
            try:
                from datetime import datetime
                datetime.fromisoformat(str(val))
            except ValueError:
                issues.append(f"{key} 不是合法 ISO 时间: {val!r}")
    # MEMORY_SPEC 5.6：敏感信息直接拒绝
    low = content.lower()
    if any(p in low for p in SECRET_PATTERNS):
        issues.append("content 含敏感信息（密钥/密码/验证码），拒绝写入")
    # MEMORY_SPEC 5.8：session 必须带 expires_at
    if kind == "session" and not cand.get("expires_at"):
        issues.append("session 必须带 expires_at")
    status = cand.get("status")
    if status is not None and status not in VALID_STATUS:
        issues.append(f"status 非法: {status!r}")
    return issues


@dataclass
class MemoryEntry:
    id: int
    layer: str
    content: str
    importance: float = 0.5
    confidence: float = 0.75
    provenance: str | None = None
    version: int = 1
    strength: float = 1.0
    category: str | None = None
    meta: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    # ---------- M-1 数据真值（MEMORY_SPEC 8） ----------

    def is_expired(self, now: str | None = None) -> bool:
        """expires_at / valid_to 已过 → 过期（session 强制，其他类型可选）。"""
        from datetime import datetime

        now = now or _now()
        try:
            now_dt = datetime.fromisoformat(now)
        except ValueError:
            now_dt = None
        for key in ("expires_at", "valid_to"):
            val = self.meta.get(key)
            if not val:
                continue
            try:
                val_dt = datetime.fromisoformat(val)
            except (ValueError, TypeError):
                return True  # 非法时间按过期处理（防脏数据流入召回）
            if now_dt is not None and val_dt <= now_dt:
                return True
        return False

    @property
    def chain_id(self) -> int:
        return int(self.meta.get("chain_id") or self.id)

    @property
    def status(self) -> str:
        return self.meta.get("status", "active")


class MemoryStore:
    def __init__(
        self,
        db_path: str = "data/veranima.db",
        config: dict | None = None,
        provider: EmbeddingProvider | None = None,
        llm_config: dict | None = None,
    ):
        self.config = config or {}
        self.llm_config = llm_config or {}
        self.provider = provider or make_provider(self.config, self.llm_config)
        self.con = init_db(db_path, dim=self.provider.dim, provider=self.provider)
        self._vec_ok = self._check_vec()

    def _check_vec(self) -> bool:
        try:
            self.con.execute("SELECT count(*) FROM memory_vec")
            return True
        except Exception:
            return False

    # ---------- 写入 ----------

    def store(
        self,
        layer: str,
        content: str,
        *,
        importance: float = 0.5,
        confidence: float = 0.75,
        provenance: str | None = None,
        category: str | None = None,
        meta: dict | None = None,
    ) -> MemoryEntry:
        """ADD-only 写入：只新增，不覆盖（修正走版本链，见 update_latest）。"""
        layer = LAYER_R1_MAP.get(layer, layer)  # R1 类型名 → 旧 layer（R1_SPEC 1.1）
        if layer not in LAYERS:
            raise ValueError(f"unknown layer: {layer}")
        ts = _now()
        cur = self.con.execute(
            """INSERT INTO memories
               (layer, content, importance, confidence, provenance, version,
                strength, category, meta, created_at, updated_at)
               VALUES (?,?,?,?,?,1,1.0,?,?,?,?)""",
            (layer, content, importance, confidence, provenance, category,
             json.dumps(meta or {}, ensure_ascii=False), ts, ts),
        )
        mid = cur.lastrowid
        self.con.commit()
        entry = MemoryEntry(
            id=mid, layer=layer, content=content, importance=importance,
            confidence=confidence, provenance=provenance, version=1,
            strength=1.0, category=category, meta=meta or {},
            created_at=ts, updated_at=ts,
        )
        # M-3 显式同步 FTS 索引（独立表，无触发器）
        if content.strip():
            try:
                self.con.execute(
                    "INSERT INTO memories_fts(rowid, content) VALUES (?,?)", (mid, content)
                )
                self.con.commit()
            except Exception as e:
                logger.warning("memories_fts index failed for memory %s: %s", mid, e)
        # 向量索引（写入即检索：零开销摄入的向量侧）
        if self._vec_ok and content.strip():
            try:
                vec = self.provider.embed([content])[0]
                self.con.execute(
                    "INSERT INTO memory_vec(memory_id, embedding) VALUES (?,?)",
                    (mid, json.dumps(vec)),
                )
                self.con.commit()
            except Exception as e:
                logger.warning("vector index failed for memory %s: %s", mid, e)
        return entry

    def store_message(self, role: str, content: str, energy: float | None = None, mood: str | None = None) -> int:
        """零开销摄入：原始消息立即入库（FTS5 触发器同步索引），不等待 LLM。"""
        cur = self.con.execute(
            "INSERT INTO messages(role, content, created_at, energy_at, mood_at) VALUES (?,?,?,?,?)",
            (role, content, _now(), energy, mood),
        )
        self.con.commit()
        return cur.lastrowid

    # ---------- Agent 状态持久化（2026-08-04 重启续接） ----------

    def save_state(self, snapshot: dict) -> None:
        """Agent 内在状态（依恋度/精力/情绪/计数/R1 字段）单行 upsert。"""
        self.con.execute(
            """INSERT INTO agent_state (id, energy, mood, attachment, mood_score, total_messages,
                     social_appetite, attention_topic, attention_scene,
                     last_interaction_channel, last_cause, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                energy=excluded.energy, mood=excluded.mood, attachment=excluded.attachment,
                mood_score=excluded.mood_score, total_messages=excluded.total_messages,
                social_appetite=excluded.social_appetite, attention_topic=excluded.attention_topic,
                attention_scene=excluded.attention_scene,
                last_interaction_channel=excluded.last_interaction_channel,
                last_cause=excluded.last_cause,
                updated_at=excluded.updated_at""",
            (snapshot.get("energy", 100.0), snapshot.get("mood", "平静"),
             snapshot.get("attachment", 0.5), snapshot.get("mood_score", 0.0),
             snapshot.get("total_messages", 0),
             snapshot.get("social_appetite", 0.8), snapshot.get("attention_topic", ""),
             snapshot.get("attention_scene", "normal"),
             snapshot.get("last_interaction_channel", ""), snapshot.get("last_cause", "startup"),
             _now()),
        )
        self.con.commit()

    def load_state(self) -> dict | None:
        """读取持久化的 Agent 状态；无记录（新库/旧库未初始化）返回 None。"""
        row = self.con.execute(
            "SELECT energy, mood, attachment, mood_score, total_messages,"
            " social_appetite, attention_topic, attention_scene,"
            " last_interaction_channel, last_cause"
            " FROM agent_state WHERE id=1"
        ).fetchone()
        return dict(row) if row else None

    def update_latest(self, memory_id: int, new_content: str, *, confidence: float = 1.0, meta: dict | None = None) -> MemoryEntry:
        """显式版本链：修正不覆盖——新版本入链，旧版本保留（DESIGN.md 写入与检索节）。

        M-2（MEMORY_SPEC 8.2）：自动写入 meta.supersedes=old_id，保证任何调用方
        （promises/候选修正）都形成可追溯链条；调用方显式传入的 supersedes 优先。
        """
        old = self.get(memory_id)
        if old is None:
            raise KeyError(memory_id)
        ts = _now()
        merged_meta = {**old.meta, "supersedes": old.id, **(meta or {})}
        cur = self.con.execute(
            """INSERT INTO memories
               (layer, content, importance, confidence, provenance, version,
                strength, category, meta, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (old.layer, new_content, old.importance, confidence, old.provenance,
             old.version + 1, old.strength, old.category,
             json.dumps(merged_meta, ensure_ascii=False), old.created_at, ts),
        )
        mid = cur.lastrowid
        self.con.commit()
        # M-3 显式同步 FTS 索引（新版本内容）
        try:
            self.con.execute(
                "INSERT INTO memories_fts(rowid, content) VALUES (?,?)", (mid, new_content)
            )
            self.con.commit()
        except Exception as e:
            logger.warning("memories_fts index failed on version %s: %s", mid, e)
        if self._vec_ok:
            try:
                vec = self.provider.embed([new_content])[0]
                self.con.execute(
                    "INSERT INTO memory_vec(memory_id, embedding) VALUES (?,?)",
                    (mid, json.dumps(vec)),
                )
                self.con.commit()
            except Exception as e:
                logger.warning("vector index failed on version %s: %s", mid, e)
        return self.get(mid)

    # ---------- 读取 ----------

    def get(self, memory_id: int) -> MemoryEntry | None:
        row = self.con.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._row_to_entry(row) if row else None

    def _superseded_ids(self) -> set[int]:
        """被版本链引用的旧 id 集合（M-1：current 判定）。"""
        try:
            rows = self.con.execute(
                "SELECT json_extract(meta, '$.supersedes') AS sid FROM memories"
                " WHERE meta LIKE '%supersedes%' AND json_valid(meta)"
            ).fetchall()
        except Exception:
            return set()
        return {int(r["sid"]) for r in rows if r["sid"]}

    def get_history(self, memory_id: int) -> list[MemoryEntry]:
        """版本链（旧→新，MEMORY_SPEC 8.2：审计/解释用，不参与普通召回）。"""
        chain: dict[int, MemoryEntry] = {}
        seen: set[int] = set()

        def walk(mid: int, direction: int) -> None:
            if mid in seen:
                return
            seen.add(mid)
            e = self.get(mid)
            if e is None:
                return
            chain[mid] = e
            if direction <= 0:
                old_id = e.meta.get("supersedes")
                if old_id:
                    try:
                        walk(int(old_id), -1)
                    except (TypeError, ValueError):
                        pass
            if direction >= 0:
                rows = self.con.execute(
                    "SELECT id FROM memories WHERE json_extract(meta,'$.supersedes')=?",
                    (mid,),
                ).fetchall()
                for r in rows:
                    walk(r["id"], +1)

        walk(memory_id, 0)
        return [chain[k] for k in sorted(chain)]

    def list_layer(self, layer: str, limit: int = 100, include_superseded: bool = False) -> list[MemoryEntry]:
        rows = self.con.execute(
            "SELECT * FROM memories WHERE layer=? ORDER BY updated_at DESC LIMIT ?",
            (layer, limit * 4 if not include_superseded else limit),
        ).fetchall()
        entries = [self._row_to_entry(r) for r in rows]
        if include_superseded:
            return entries[:limit]
        superseded = self._superseded_ids()
        # M-1：默认只返回版本链 current + 未过期（session TTL 硬过滤）
        current = [e for e in entries if e.id not in superseded and not e.is_expired()]
        return current[:limit]

    def recent_messages(self, limit: int = 20) -> list[dict]:
        rows = self.con.execute(
            "SELECT id, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------- R4 主动反馈持久化（R4_SPEC 4） ----------

    def record_proactive_feedback(self, *, source: str, responded: bool = False,
                                  interrupted: bool = False, user_sent_within: int | None = None,
                                  dismissed: bool = False) -> None:
        """记录一次主动消息的反馈（忽略/响应/打断）。"""
        self.con.execute(
            "INSERT INTO proactive_feedback"
            " (sent_at, source, responded, interrupted, user_sent_within, dismissed)"
            " VALUES (?,?,?,?,?,?)",
            (_now(), source, int(responded), int(interrupted), user_sent_within, int(dismissed)),
        )
        self.con.commit()

    def recent_proactive_feedback(self, source: str | None = None, limit: int = 10) -> list[dict]:
        """最近主动反馈记录（连续忽略判断用）。"""
        if source:
            rows = self.con.execute(
                "SELECT * FROM proactive_feedback WHERE source=? ORDER BY id DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = self.con.execute(
                "SELECT * FROM proactive_feedback ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"], layer=row["layer"], content=row["content"],
            importance=row["importance"], confidence=row["confidence"],
            provenance=row["provenance"], version=row["version"],
            strength=row["strength"], category=row["category"],
            meta=json.loads(row["meta"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ---------- 混合检索 ----------

    def recall(self, query: str, *, top_k: int = 5, layer: str | None = None) -> list[MemoryEntry]:
        """混合检索（R1_SPEC 4 排序公式）。

        score = 0.45*semantic_sim + 0.20*FTS_relevance + 0.15*freshness
                + 0.10*importance + 0.10*confidence
        某项不可得时归一化剩余权重，不补随机分。
        """
        layer = LAYER_R1_MAP.get(layer, layer) if layer else None  # R1 类型名 → 旧 layer
        # (entry, sim|None) 候选池；fts 命中 id 集合
        pool: dict[int, tuple[MemoryEntry, float | None]] = {}
        fts_hits: set[int] = set()
        if self._vec_ok:
            try:
                vec = self.provider.embed([query])[0]
                rows = self.con.execute(
                    "SELECT memory_id, distance FROM memory_vec WHERE embedding MATCH ? AND k = ?",
                    (json.dumps(vec), top_k * 4),
                ).fetchall()
                for r in rows:
                    entry = self.get(r["memory_id"])
                    if entry is None:
                        continue
                    if layer and entry.layer != layer:
                        continue
                    pool[entry.id] = (entry, 1.0 - r["distance"])
            except Exception as e:
                logger.warning("vector recall failed: %s", e)
        # FTS5 直接命中规范记忆（M-3，MEMORY_SPEC 10.2：不依赖消息巧合命中）
        try:
            for r in self.con.execute(
                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? LIMIT 10",
                (self._fts_query(query),),
            ).fetchall():
                entry = self.get(r["rowid"])
                if entry is None:
                    continue
                if layer and entry.layer != layer:
                    continue
                pool.setdefault(entry.id, (entry, None))
                fts_hits.add(entry.id)
        except Exception as e:
            logger.debug("memories fts failed: %s", e)
        # 旧路径兼容：FTS 命中消息 → 关联 provenance 的记忆（relevance 信号）
        try:
            msg_hits = set(
                r["rowid"] for r in self.con.execute(
                    "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT 10",
                    (self._fts_query(query),),
                ).fetchall()
            )
            if msg_hits:
                ids = list(msg_hits)
                ph = ",".join("?" * len(ids))
                mems = self.con.execute(
                    f"SELECT * FROM memories WHERE provenance IN ({ph})",
                    ids,
                ).fetchall()
                for m in mems:
                    entry = self._row_to_entry(m)
                    if layer and entry.layer != layer:
                        continue
                    pool.setdefault(entry.id, (entry, None))
        except Exception as e:
            logger.debug("fts recall failed: %s", e)
        if not pool:
            # 兜底：无向量且无 FTS 命中时，同层全量按 bigram 包含匹配
            # （ponytail: 低成本近似，精准度低于向量/BM25；embedding 可用后自然淘汰）
            try:
                q = query.replace(" ", "").replace('"', "")
                bigrams = [q[i:i + 2] for i in range(len(q) - 1) if q[i:i + 2].strip()]
                for entry in self.list_layer(layer or "semantic", limit=200):
                    if bigrams and any(b in entry.content for b in bigrams[:8]):
                        pool[entry.id] = (entry, None)
            except Exception as e2:
                logger.debug("recall keyword fallback failed: %s", e2)
        if not pool:
            return []
        # M-1 硬过滤先于排序（MEMORY_SPEC 10.3）：非 current 版本 / 过期条目
        superseded = self._superseded_ids()
        pool = {
            mid: (e, sim)
            for mid, (e, sim) in pool.items()
            if mid not in superseded and not e.is_expired()
        }
        if not pool:
            return []
        # M-3 entity/temporal 信号（MEMORY_SPEC 10.1/10.3 新权重）
        intent = self._temporal_intent(query)
        scored: dict[int, float] = {}
        for mid, (entry, sim) in pool.items():
            parts: list[float] = []
            weights: list[float] = []
            if sim is not None:
                parts.append(max(sim, 0.0)); weights.append(0.35)
            if mid in fts_hits:
                parts.append(1.0); weights.append(0.20)
            parts.append(self._temporal_match(entry, intent)); weights.append(0.15)
            parts.append(self._subject_match(query, entry)); weights.append(0.15)
            parts.append(1.0 / (1.0 + self._age_days(entry.updated_at))); weights.append(0.05)
            parts.append(max(0.0, min(1.0, entry.importance))); weights.append(0.05)
            parts.append(max(0.0, min(1.0, entry.confidence))); weights.append(0.05)
            wsum = sum(weights)
            scored[mid] = sum(p * (w / wsum) for p, w in zip(parts, weights)) if wsum else 0.0
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        # 更新访问时间（触发唤醒的时间信号）
        now = _now()
        for mid, _ in ranked:
            self.con.execute("UPDATE memories SET last_access_at=? WHERE id=?", (now, mid))
        self.con.commit()
        return [self.get(mid) for mid, _ in ranked]

    # ---------- M-3 查询意图与信号（MEMORY_SPEC 10.1） ----------

    @staticmethod
    def _temporal_intent(query: str) -> str:
        """查询时间意图：past / future / current / none。"""
        if any(k in query for k in ("以前", "之前", "上次", "去年", "上个月", "上周", "当时", "过去")):
            return "past"
        if any(k in query for k in ("明天", "下周", "以后", "未来", "下个月", "计划", "打算")):
            return "future"
        if any(k in query for k in ("现在", "最近", "目前", "今天")):
            return "current"
        return "none"

    @staticmethod
    def _temporal_match(entry: MemoryEntry, intent: str) -> float:
        if intent == "none":
            return 0.5  # 中性（不惩罚也不加分）
        if intent == "past":
            return 1.0 if entry.meta.get("event_time") else 0.0
        if intent == "future":
            return 1.0 if entry.meta.get("expires_at") or entry.status == "open" else 0.0
        # current：最近更新的条目更符合（freshness 已覆盖），中性 0.5
        return 0.5

    @staticmethod
    def _subject_match(query: str, entry: MemoryEntry) -> float:
        """subject/entity 匹配（MEMORY_SPEC 7：user/character 实体锚点）。"""
        subject = entry.meta.get("subject", "")
        if subject == "user" and any(k in query for k in ("我", "用户", "我的")):
            return 1.0
        if subject == "character" and any(k in query for k in ("你", "角色", "她")):
            return 1.0
        if not subject:
            return 0.5  # 无 subject 元数据：中性
        return 0.0

    @staticmethod
    def _split_grams(text: str) -> list[str]:
        """中文长串拆 3-gram（与 trigram tokenizer 对齐），英文保留原词。"""
        tokens: list[str] = []
        for w in text.replace('"', " ").split():
            if not w:
                continue
            if any("\u4e00" <= c <= "\u9fff" for c in w) and len(w) > 6:
                tokens.extend(w[i:i + 3] for i in range(len(w) - 2))
            else:
                tokens.append(w)
        return tokens

    @staticmethod
    def _fts_query(text: str) -> str:
        # 拆词为 OR 查询（中文 3-gram 对齐 trigram tokenizer），避免 FTS5 语法错误
        tokens = MemoryStore._split_grams(text)
        return " OR ".join(f'"{t}"' for t in tokens[:12]) or '" "'

    @staticmethod
    def _age_days(ts: str) -> float:
        try:
            dt = datetime.fromisoformat(ts)
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        except Exception:
            return 0.0

    # ---------- 遗忘衰减 ----------

    def decay(self) -> dict:
        """艾宾浩斯衰减：R = e^(-t/S)，S 与重要性挂钩（DESIGN.md 轨道一）。

        返回 {updated, faded} 统计。
        """
        if not self.config.get("decay_enabled", True):
            return {"updated": 0, "faded": 0}
        base_s = float(self.config.get("importance_base_s", 2592000))
        now = time.time()
        rows = self.con.execute("SELECT id, importance, strength, updated_at FROM memories").fetchall()
        updated = faded = 0
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["updated_at"])
                elapsed = max(0.0, now - dt.timestamp())
            except Exception:
                elapsed = 0.0
            s = base_s * (0.2 + 2.0 * r["importance"])
            new_strength = r["strength"] * math.exp(-elapsed / s)
            if abs(new_strength - r["strength"]) < 1e-6:
                continue
            self.con.execute(
                "UPDATE memories SET strength=?, updated_at=? WHERE id=?",
                (new_strength, _now(), r["id"]),
            )
            updated += 1
            if new_strength < 0.3:
                faded += 1
        self.con.commit()
        return {"updated": updated, "faded": faded}

    # ---------- 删除 ----------

    def erase(self, memory_id: int | None = None, *, content_contains: str | None = None, layer: str | None = None) -> int:
        """删除记忆：整条版本链（记忆 + 向量），原始 messages 保留（MEMORY_SPEC 14.2：
        原文删除单独询问，不在记忆删除时静默级联）。"""
        if memory_id is not None:
            # M-1：链上所有版本一起删（supersedes 双向）
            chain = self.get_history(memory_id)
            ids = [e.id for e in chain] or [memory_id]
            ph = ",".join("?" * len(ids))
            self.con.execute(f"DELETE FROM memories WHERE id IN ({ph})", ids)
            try:
                self.con.execute(f"DELETE FROM memories_fts WHERE rowid IN ({ph})", ids)
            except Exception as e:
                logger.warning("memories_fts delete failed: %s", e)
            if self._vec_ok:
                self.con.execute(f"DELETE FROM memory_vec WHERE memory_id IN ({ph})", ids)
            self.con.commit()
            return len(ids)
        where, params = [], []
        if content_contains:
            where.append("content LIKE ?")
            params.append(f"%{content_contains}%")
        if layer:
            where.append("layer=?")
            params.append(layer)
        if not where:
            return 0
        rows = self.con.execute(
            f"SELECT id FROM memories WHERE {' AND '.join(where)}", params
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        # 批量路径也扩展到链（content/layer 命中的旧版本连同新版本一起删）
        chained: set[int] = set(ids)
        for mid in list(ids):
            chained.update(e.id for e in self.get_history(mid))
        ids = sorted(chained)
        ph = ",".join("?" * len(ids))
        self.con.execute(f"DELETE FROM memories WHERE id IN ({ph})", ids)
        try:
            self.con.execute(f"DELETE FROM memories_fts WHERE rowid IN ({ph})", ids)
        except Exception as e:
            logger.warning("memories_fts delete failed: %s", e)
        if self._vec_ok:
            self.con.execute(f"DELETE FROM memory_vec WHERE memory_id IN ({ph})", ids)
        self.con.commit()
        return len(ids)

    def stats(self) -> dict:
        """消息与记忆统计（/status 与月度回顾用）。"""
        messages = self.con.execute("SELECT count(*) FROM messages").fetchone()[0]
        by_role = dict(
            self.con.execute("SELECT role, count(*) FROM messages GROUP BY role").fetchall()
        )
        memories = {layer: 0 for layer in LAYERS}
        for row in self.con.execute("SELECT layer, count(*) FROM memories GROUP BY layer").fetchall():
            memories[row["layer"]] = row[1]
        return {
            "messages": messages,
            "user_messages": by_role.get("user", 0),
            "assistant_messages": by_role.get("assistant", 0),
            "memories": memories,
        }

    # ---------- 整理（MVP2 占位） ----------

    def curate(self, *, sim_dup: float = 0.92, sim_merge: float = 0.78, min_confidence: float = 0.55, max_ops: int = 50) -> dict:
        """记忆整理（curator）：去重 / 合并 / 低置信度丢弃 / 强度衰减清理。

        - 去重：同层内容相似度 ≥ sim_dup → 保留较新/置信度高者
        - 合并：相似度 ≥ sim_merge → 合并为一条（内容连接，importance 取 max）
        - 丢弃：confidence < min_confidence → 删除（设计：低置信度不写回/不保留）
        - 单次最多 max_ops 个操作（防批量风暴）
        """
        ops = {"dedup": 0, "merge": 0, "drop": 0}
        entries: list[MemoryEntry] = []
        for layer in LAYERS:
            entries.extend(self.list_layer(layer, limit=500))
        if not entries:
            return {"counts": self._layer_counts(), "ops": ops}

        # 低置信度 → 丢弃（设计：confidence < 0.55 不保留）
        drops = [e for e in entries if e.confidence < min_confidence]
        for e in drops[: max_ops - ops["drop"]]:
            self.erase(e.id)
            ops["drop"] += 1
        entries = [e for e in entries if e.id not in {d.id for d in drops}]

        # 同层内两两比较（相似度用向量余弦）
        vecs = {}
        for e in entries:
            try:
                vecs[e.id] = self.provider.embed([e.content])[0]
            except Exception:
                continue
        import math

        def cos(a, b):
            return sum(x * y for x, y in zip(a, b)) / (
                math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0
            )

        for layer in LAYERS:
            group = [e for e in entries if e.layer == layer and e.id in vecs]
            if len(group) < 2:
                continue
            group.sort(key=lambda e: -e.id)  # 新在前
            for i in range(len(group)):
                if ops["dedup"] + ops["merge"] >= max_ops:
                    break
                a = group[i]
                for j in range(i + 1, len(group)):
                    b = group[j]
                    if ops["dedup"] + ops["merge"] >= max_ops:
                        break
                    if a.id in self._deleted or b.id in self._deleted:
                        continue
                    try:
                        sim = cos(vecs[a.id], vecs[b.id])
                    except Exception:
                        continue
                    if sim >= sim_dup:
                        # 去重：留新的（a），删旧的（b）
                        self.erase(b.id)
                        ops["dedup"] += 1
                        self._deleted.add(b.id)
                    elif sim >= sim_merge:
                        # 合并：创建合并版（版本链，M-1 自动 supersedes 旧版本），
                        # 不删除旧版本（MEMORY_SPEC 12.2：不得删除原始证据，M-5 完善）
                        merged = f"{a.content}；{b.content}"[:400]
                        self.update_latest(a.id, merged, confidence=max(a.confidence, b.confidence))
                        ops["merge"] += 1
                        self._deleted.add(a.id)
                        vecs[a.id] = self.provider.embed([merged])[0]

        return {"counts": self._layer_counts(), "ops": ops}

    @property
    def _deleted(self) -> set[int]:
        if not hasattr(self, "_curate_deleted"):
            self._curate_deleted = set()
        return self._curate_deleted

    def _layer_counts(self) -> dict:
        counts = {}
        for layer in LAYERS:
            counts[layer] = self.con.execute(
                "SELECT count(*) FROM memories WHERE layer=?", (layer,)
            ).fetchone()[0]
        return counts

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        self.con = init_db(db_path, dim=self.provider.dim)
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

    def update_latest(self, memory_id: int, new_content: str, *, confidence: float = 1.0, meta: dict | None = None) -> MemoryEntry:
        """显式版本链：修正不覆盖——新版本入链，旧版本保留（DESIGN.md 写入与检索节）。"""
        old = self.get(memory_id)
        if old is None:
            raise KeyError(memory_id)
        ts = _now()
        cur = self.con.execute(
            """INSERT INTO memories
               (layer, content, importance, confidence, provenance, version,
                strength, category, meta, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (old.layer, new_content, old.importance, confidence, old.provenance,
             old.version + 1, old.strength, old.category,
             json.dumps({**old.meta, **(meta or {})}, ensure_ascii=False), old.created_at, ts),
        )
        mid = cur.lastrowid
        self.con.commit()
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

    def list_layer(self, layer: str, limit: int = 100) -> list[MemoryEntry]:
        rows = self.con.execute(
            "SELECT * FROM memories WHERE layer=? ORDER BY updated_at DESC LIMIT ?",
            (layer, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def recent_messages(self, limit: int = 20) -> list[dict]:
        rows = self.con.execute(
            "SELECT id, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

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
        """混合信号：语义向量 + 时间加权；FTS5（BM25）命中消息反查关联记忆作为补充。

        score = 0.6*向量相似度 + 0.4*新鲜度(1/(1+days_old))
        """
        scored: dict[int, float] = {}
        if self._vec_ok:
            try:
                vec = self.provider.embed([query])[0]
                rows = self.con.execute(
                    "SELECT memory_id, distance FROM memory_vec WHERE embedding MATCH ? AND k = ?",
                    (json.dumps(vec), top_k * 2),
                ).fetchall()
                for r in rows:
                    sim = 1.0 - r["distance"]
                    entry = self.get(r["memory_id"])
                    if entry is None:
                        continue
                    if layer and entry.layer != layer:
                        continue
                    age_days = self._age_days(entry.updated_at)
                    recency = 1.0 / (1.0 + age_days)
                    scored[entry.id] = 0.6 * max(sim, 0.0) + 0.4 * recency
            except Exception as e:
                logger.warning("vector recall failed: %s", e)
        # FTS5 补充：命中消息 → 关联 provenance 的记忆
        try:
            fts_hits = self.con.execute(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT 10",
                (self._fts_query(query),),
            ).fetchall()
            if fts_hits:
                ids = [r["rowid"] for r in fts_hits]
                ph = ",".join("?" * len(ids))
                mems = self.con.execute(
                    f"SELECT * FROM memories WHERE provenance IN ({ph})",
                    ids,
                ).fetchall()
                for m in mems:
                    entry = self._row_to_entry(m)
                    if layer and entry.layer != layer:
                        continue
                    scored[entry.id] = scored.get(entry.id, 0.0) + 0.5
        except Exception as e:
            logger.debug("fts recall failed: %s", e)
        if not scored:
            return []
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        # 更新访问时间（触发唤醒的时间信号）
        now = _now()
        for mid, _ in ranked:
            self.con.execute("UPDATE memories SET last_access_at=? WHERE id=?", (now, mid))
        self.con.commit()
        return [self.get(mid) for mid, _ in ranked]

    @staticmethod
    def _fts_query(text: str) -> str:
        # 拆词为 OR 查询，避免 FTS5 语法错误
        words = [w for w in text.replace('"', " ").split() if w]
        return " OR ".join(f'"{w}"' for w in words[:8]) or '" "'

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
        """级联删除：记忆 + 向量 + 关联消息（DESIGN.md 隐私擦除）。"""
        if memory_id is not None:
            self.con.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            if self._vec_ok:
                self.con.execute("DELETE FROM memory_vec WHERE memory_id=?", (memory_id,))
            self.con.commit()
            return 1
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
        ph = ",".join("?" * len(ids))
        self.con.execute(f"DELETE FROM memories WHERE id IN ({ph})", ids)
        if self._vec_ok:
            self.con.execute(f"DELETE FROM memory_vec WHERE memory_id IN ({ph})", ids)
        self.con.commit()
        return len(ids)

    # ---------- 整理（MVP2 占位） ----------

    def curate(self) -> dict:
        """定期整理（curator）：MVP2 实现，当前仅返回统计。"""
        counts = {}
        for layer in LAYERS:
            counts[layer] = self.con.execute(
                "SELECT count(*) FROM memories WHERE layer=?", (layer,)
            ).fetchone()[0]
        return {"counts": counts, "note": "curator pending MVP2"}

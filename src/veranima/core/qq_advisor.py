from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass

from .qq_proactive import QQProactiveEngine, QQProactiveState


@dataclass(frozen=True)
class QQMaterial:
    kind: str
    text: str
    confidence: float = 0.0
    source_id: int | None = None
    source_memory_id: int | None = None
    source_message_id: int | None = None


class QQProactiveAdvisor:
    """从本地 QQ 文本历史构造时机引擎输入；不读取桌宠视觉上下文。"""

    def __init__(self, memory, *, config: dict | None = None):
        self.memory = memory
        self.engine = QQProactiveEngine(config or {})
        self.state = QQProactiveState()

    def _messages(self, limit: int) -> list[dict]:
        try:
            return self.memory.recent_messages(limit=limit, channel="qq")
        except TypeError:
            return self.memory.recent_messages(limit=limit)

    def refresh_state(self) -> QQProactiveState:
        rows = self._messages(60)
        users = [row for row in rows if row.get("role") == "user"]
        if users:
            self.state.last_user_message_at = users[-1].get("created_at")
            last_text = str(users[-1].get("content") or "")
            if any(token in last_text for token in ("别主动找我", "不要主动找我", "不要打扰", "别打扰我", "可以主动找我", "恢复主动", "解除免打扰")):
                self.engine.note_user_message(self.state, last_text, at=self.state.last_user_message_at)
            elif self.state.user_state == self.state.user_state.NORMAL:
                detected = self.engine.detect_state(last_text)
                if detected != self.state.user_state.NORMAL:
                    self.engine.note_user_message(self.state, last_text, at=self.state.last_user_message_at)
        return self.state

    def last_user_text(self) -> str:
        rows = [row for row in self._messages(60) if row.get("role") == "user"]
        return str(rows[-1].get("content") or "") if rows else ""

    def note_user_message(self, text: str, *, at: str | None = None) -> None:
        self.engine.note_user_message(self.state, text, at=at)

    def momentum(self) -> float:
        rows = [r for r in self._messages(8) if r.get("role") == "user"][-3:]
        if not rows:
            return 1.0
        lengths = [min(120, len(str(r.get("content") or ""))) / 120 for r in rows]
        return max(0.5, min(1.5, 0.65 + statistics.fmean(lengths)))

    def routine_multiplier(self, now: datetime.datetime) -> float:
        rows = [r for r in self._messages(200) if r.get("role") == "user"]
        if len(rows) < 12:
            return 1.0
        buckets = [0] * 24
        for row in rows:
            try:
                stamp = datetime.datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                buckets[stamp.astimezone(now.tzinfo).hour] += 1
            except (KeyError, TypeError, ValueError):
                continue
        total = sum(buckets)
        if not total:
            return 1.0
        ratio = buckets[now.hour] / total
        if ratio < 0.20 / 24:
            return self.engine.low_activity_multiplier
        return min(1.2, max(self.engine.low_activity_multiplier, 0.8 + ratio * 24 * 0.2))

    def social_multiplier(self) -> float:
        rows = self.memory.recent_proactive_feedback(channel="qq", limit=3)
        if len(rows) < 3:
            return 1.0
        response_rate = sum(bool(row.get("responded")) for row in rows) / len(rows)
        if response_rate < 0.3:
            return 0.2
        if response_rate > 0.8:
            return 1.3
        return 1.0

    def material(self, query: str = "") -> QQMaterial:
        if query:
            for layer in ("episodic", "procedural", "semantic"):
                for entry in self.memory.recall(query, top_k=3, layer=layer):
                    meta = getattr(entry, "meta", None) or {}
                    if meta.get("kind") == "relational_tension_event":
                        continue
                    if (meta.get("kind") == "conversation_event"
                            and meta.get("status", "active") != "active"):
                        continue
                    if entry.confidence >= 0.65:
                        meta = getattr(entry, "meta", None) or {}
                        source_ids = meta.get("source_message_ids") or []
                        message_id = meta.get("source_message_id") or (source_ids[-1] if source_ids else None)
                        return QQMaterial(
                            "memory", entry.content, entry.confidence, entry.id,
                            source_memory_id=entry.id, source_message_id=message_id,
                        )
        try:
            active_events = [
                entry for entry in self.memory.list_layer("episodic", limit=100)
                if (getattr(entry, "meta", None) or {}).get("kind") == "conversation_event"
                and (getattr(entry, "meta", None) or {}).get("status", "active") == "active"
                and entry.confidence >= 0.65
            ]
            if active_events:
                entry = active_events[0]
                meta = getattr(entry, "meta", None) or {}
                source_ids = meta.get("source_message_ids") or []
                message_id = meta.get("source_message_id") or (source_ids[-1] if source_ids else None)
                return QQMaterial(
                    "memory", entry.content, entry.confidence, entry.id,
                    source_memory_id=entry.id, source_message_id=message_id,
                )
        except Exception:
            pass
        rows = self._messages(8)
        for row in reversed(rows):
            if row.get("role") == "user" and len(str(row.get("content") or "")) >= 6:
                return QQMaterial(
                    "time_followup", str(row["content"]), 0.5, row.get("id"),
                    source_message_id=row.get("id"),
                )
        return QQMaterial("presence", "", 0.0, None)

    def evaluate(self, now: datetime.datetime, *, query: str = ""):
        self.refresh_state()
        material = self.material(query)
        readiness = self.engine.evaluate(
            self.state,
            now=now,
            momentum=self.momentum(),
            routine_multiplier=self.routine_multiplier(now),
            material_multiplier=2.0 if material.confidence >= 0.8 else 1.0 if material.confidence else 0.5,
            social_multiplier=self.social_multiplier(),
        )
        return readiness, material

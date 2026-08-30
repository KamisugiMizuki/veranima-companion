"""MEMORY_SPEC 10.4 Context Brief：当轮最小相关片段，统一预算与完整 item 截断。

召回结果不能原样全塞进 prompt：
- 按层预算注入（core_profile/procedural/semantic/episodic/session）
- 超预算按完整 item 删除尾部，不在句子中间硬截断
- 每条携带 memory_id / kind / confidence_label / temporal_label / score / sensitivity
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..memory.store import MemoryEntry


@dataclass(frozen=True)
class MemoryBriefItem:
    memory_id: int
    kind: str
    text: str
    confidence_label: str
    temporal_label: str
    score: float
    sensitivity: str = "personal"
    label: str = "记忆"          # 注入时的层标签（【长期事实】等）

    def to_line(self) -> str:
        prefix = f"[{self.confidence_label}] " if self.confidence_label != "高" else ""
        return f"- {prefix}{self.text}"


DEFAULT_BUDGETS: dict[str, int] = {
    "core_profile": 1200,
    "procedural": 1000,
    "semantic": 1400,
    "episodic": 1400,
    "session": 600,
}
DEFAULT_TOTAL = 5600

LAYER_LABELS = {
    "core_profile": "常驻档案",
    "procedural": "协作规则",
    "semantic": "长期事实",
    "episodic": "共同经历",
    "session": "本次会话",
}


def confidence_label(entry: MemoryEntry) -> str:
    if entry.confidence >= 0.85:
        return "高"
    if entry.confidence >= 0.6:
        return "中"
    return "低"


def temporal_label(entry: MemoryEntry) -> str:
    if entry.meta.get("expires_at"):
        return "未来"
    if entry.meta.get("event_time"):
        return "过去"
    return "现在"


def build_brief(
    *,
    core_profile: list[MemoryEntry] | None = None,
    procedural: list[MemoryEntry] | None = None,
    semantic: list[MemoryEntry] | None = None,
    episodic: list[MemoryEntry] | None = None,
    session: list[MemoryEntry] | None = None,
    budgets: dict[str, int] | None = None,
    total_budget: int = DEFAULT_TOTAL,
    fallback_scores: dict[int, float] | None = None,
) -> list[MemoryBriefItem]:
    """按层预算构造 Context Brief。

    各层输入应为已排序（相关度降序）的条目；超预算时按完整 item 丢弃尾部。
    fallback_scores 提供无 recall 分数场景的排位（如 list_layer 顺序）。
    """
    budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
    layers = (
        ("core_profile", core_profile or []),
        ("procedural", procedural or []),
        ("semantic", semantic or []),
        ("episodic", episodic or []),
        ("session", session or []),
    )
    fallback_scores = fallback_scores or {}
    items: list[MemoryBriefItem] = []
    used = 0
    for layer, entries in layers:
        if not entries:
            continue
        budget = budgets.get(layer, 0)
        layer_used = 0
        for rank, e in enumerate(entries):
            if layer_used + len(e.content) > budget or used + len(e.content) > total_budget:
                continue  # 单条超预算 → 完整丢弃该条，不硬截断；后续短条目可进入
            score = fallback_scores.get(e.id, max(0.0, 1.0 - rank * 0.1))
            items.append(MemoryBriefItem(
                memory_id=e.id,
                kind=e.meta.get("kind", ""),
                text=e.content,
                confidence_label=confidence_label(e),
                temporal_label=temporal_label(e),
                score=score,
                sensitivity=str(e.meta.get("sensitivity", "personal")),
                label=LAYER_LABELS.get(layer, layer),
            ))
            layer_used += len(e.content)
            used += len(e.content)
    return items


def format_brief(items: list[MemoryBriefItem]) -> str:
    """渲染为 prompt 注入文本（按层分组，带来源标签）。

    同层内 content 完全相同的条目只保留第一条（指令稀释：重复模板行占预算又降噪）。
    """
    by_label: dict[str, list[MemoryBriefItem]] = {}
    seen: dict[str, set[str]] = {}
    for it in items:
        content = it.text.strip()
        if content in seen.setdefault(it.label, set()):
            continue
        seen[it.label].add(content)
        by_label.setdefault(it.label, []).append(it)
    parts: list[str] = []
    for label, group in by_label.items():
        lines = "\n".join(it.to_line() for it in group)
        parts.append(f"【{label}】\n{lines}")
    return "\n\n".join(parts)


# ---------- MEMORY_SPEC 9 历史压缩 ----------

@dataclass
class HistorySummary:
    """压缩历史摘要：结构化历史的一部分（非普通消息），带消息区间证据。"""

    summary: str
    from_message_id: int
    to_message_id: int
    source_count: int
    created_at: str = ""
    facts: list[str] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)

    def to_session_meta(self) -> dict:
        return {
            "kind": "history_summary",
            "from_message_id": self.from_message_id,
            "to_message_id": self.to_message_id,
            "source_count": self.source_count,
        }

    @classmethod
    def from_entry(cls, entry: MemoryEntry) -> "HistorySummary":
        m = entry.meta
        return cls(
            summary=entry.content,
            from_message_id=int(m.get("from_message_id", 0)),
            to_message_id=int(m.get("to_message_id", 0)),
            source_count=int(m.get("source_count", 0)),
            created_at=entry.created_at,
        )


"""VISION_SPEC 1：视觉注意力数据结构契约。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


def _new_event_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class AttentionInput:
    """L0 输入快照（VISION_SPEC 1）。"""

    foreground_app: str = ""
    foreground_category: str = ""
    cursor_pos: tuple[int, int] | None = None
    idle_seconds: float = 0.0
    locked: bool = False
    frame: object | None = None
    captured_at: float = 0.0


@dataclass
class AttentionEvent:
    """注意力事件（VISION_SPEC 1）：pet_server/R4 消费。

    旧字段 kind/region/tag/note/ts 保留兼容；新增 event_id/confidence/
    source/reason/expires_at 逐步接入。
    """

    kind: str          # window_switch/focus_shift/habituated/away/fixation_shift
    region: tuple = (0, 0, 1, 1)
    tag: str = ""
    note: str = ""
    ts: float = field(default_factory=time.time)
    event_id: str = field(default_factory=_new_event_id)
    confidence: float = 0.7
    source: str = "saliency"   # foreground/cursor/saliency/manual
    reason: str = ""
    app_name: str = ""
    window_title: str = ""
    window_category: str = ""
    expires_at: float = 0.0

    def __post_init__(self):
        if not self.expires_at:
            self.expires_at = self.ts + 60.0  # 默认事件 TTL 60s


@dataclass(frozen=True)
class Observation:
    """L3 观察结果（VISION_SPEC 1/6）：短期情境，不写长期记忆。"""

    event_id: str = field(default_factory=_new_event_id)
    summary: str = ""
    category: str = "unknown"    # coding/browser/game/video/meeting/private/unknown
    notable: tuple = ()          # 最多 3 项（VISION_SPEC 6）
    confidence: float = 0.0
    sensitive_redacted: bool = False
    expires_at: float = field(default_factory=lambda: time.time() + 600)  # TTL 10min

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """失败观察（VISION_SPEC 6：任何失败返回 category=unknown, confidence=0）。"""
        return self.category != "unknown" and self.confidence > 0.0

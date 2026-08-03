"""Agent 内在状态：精力 / 情绪 / 依恋度（DESIGN.md 行为模式章节）。

- 精力 0-100：随时间衰减，对话恢复；低精力时回复简短
- 情绪：由近期互动驱动（简单规则：积极互动↑，消极↓）
- 依恋度 0-1：早期增长快、后期趋稳（logistic 型）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Mood = Literal["开心", "平静", "低落", "期待"]


@dataclass
class AgentState:
    energy: float = 100.0
    mood: Mood = "平静"
    attachment: float = 0.0
    # 内部
    _last_tick: float = field(default=0.0, repr=False)
    _mood_score: float = field(default=0.0, repr=False)   # 近期互动累积
    _total_messages: int = field(default=0, repr=False)

    def __post_init__(self):
        self._last_tick = time.time()

    # ---------- 时间推进 ----------

    def tick(self, decay_per_minute: float = 0.02) -> None:
        """按流逝时间衰减精力（调用方定期触发，如每条消息前）。"""
        now = time.time()
        dt_min = (now - self._last_tick) / 60.0
        self._last_tick = now
        self.energy = max(0.0, min(100.0, self.energy - decay_per_minute * dt_min))

    # ---------- 对话反馈 ----------

    def on_user_message(self, *, positive: bool = True, recover_per_message: float = 3.0) -> None:
        """用户消息反馈：恢复精力，累积情绪分。"""
        self.energy = min(100.0, self.energy + recover_per_message)
        self._mood_score += 1.0 if positive else -1.0
        self._total_messages += 1
        self._update_mood()
        self._update_attachment()

    def on_assistant_message(self) -> None:
        self._total_messages += 1

    def _update_mood(self) -> None:
        s = self._mood_score
        if s >= 3:
            self.mood = "开心"
        elif s >= 0:
            self.mood = "平静"
        elif s >= -3:
            self.mood = "低落"
        else:
            self.mood = "低落"
            self._mood_score = -3  # 钳制

    def _update_attachment(self, growth: float = 0.05) -> None:
        """依恋度：早期快后期稳（logistic 型，cap 0.95）。"""
        # 每轮增长随当前值递减
        step = growth * (1.0 - self.attachment / 0.95)
        self.attachment = min(0.95, self.attachment + step)

    # ---------- 状态描述（注入 prompt） ----------

    def to_prompt_block(self) -> str:
        energy_desc = (
            "精力充沛，主动热情" if self.energy > 70 else
            "精力一般，正常回应" if self.energy > 40 else
            "有些疲惫，回应从简，可以坦诚表达累"
        )
        return (
            f"【当前状态】情绪：{self.mood}；{energy_desc}。"
            f"依恋度：{self.attachment:.2f}（0=初识，1=亲密）。"
        )

    def summary(self) -> dict:
        return {
            "energy": round(self.energy, 1),
            "mood": self.mood,
            "attachment": round(self.attachment, 3),
            "total_messages": self._total_messages,
        }

    @property
    def total_messages(self) -> int:
        return self._total_messages

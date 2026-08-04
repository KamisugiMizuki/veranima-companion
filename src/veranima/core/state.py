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
    # 初始依恋度（DESIGN.md 第 6 章 2026-08：默认 0.5 半亲密起步，跳过初识期）
    initial_attachment: float = 0.5
    # 内部
    _last_tick: float = field(default=0.0, repr=False)
    _mood_score: float = field(default=0.0, repr=False)   # 近期互动累积
    _total_messages: int = field(default=0, repr=False)

    def __post_init__(self):
        self._last_tick = time.time()
        if self.attachment == 0.0:
            self.attachment = max(0.0, min(0.95, self.initial_attachment))

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
            "有些疲惫，回应从简，可以坦诚表达累；可以自然带一点疲惫的小动作（如打个哈欠、揉揉眼睛），自然就好，不要每句都带"
        )
        # 关系阶段（依恋度驱动）：初识→熟悉→亲密→稳定，说话方式不同
        stage, stage_desc = self.relationship_stage()
        return (
            f"【当前状态】情绪：{self.mood}；{energy_desc}。"
            f"依恋度：{self.attachment:.2f}（0=初识，1=亲密）。"
            f"【关系阶段】{stage}：{stage_desc}"
        )

    def relationship_stage(self) -> tuple[str, str]:
        """依恋度 → 关系阶段与语气指导（DESIGN.md 第 6 节）。"""
        a = self.attachment
        if a < 0.2:
            return "初识期", "礼貌克制，回应得体，不过分亲近；主要听用户讲"
        if a < 0.5:
            return "熟悉期", "自然放松，可以开轻松的玩笑，有自己的观点和小偏好；偶尔主动分享生活细节"
        if a < 0.8:
            return "亲密期", "默契自然，说话随意，可以调侃；主动分享更多生活细节，关心用户日常"
        return "稳定相伴期", "自然如常，安静陪伴；不必刻意表现亲密，稳稳地在就行"

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

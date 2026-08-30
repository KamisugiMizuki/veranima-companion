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
    # R1 状态契约（R1_SPEC 5）：社会性/注意力/因果可解释
    social_appetite: float = 0.8       # 社交欲 0-1：随时间衰减，互动恢复
    attention_topic: str = ""          # 当前注意力话题（视觉焦点/最近话题）
    attention_scene: str = "normal"    # 场景：normal/busy/away
    last_interaction_channel: str = "" # 最近互动通道：im/tts/qq
    last_cause: str = "startup"        # 最近一次状态变更原因
    # P-3（PERSONA_LOOP_SPEC）：PAD 情绪向量（0-1，向 0.5 基线衰减）
    valence: float = 0.5
    arousal: float = 0.5
    dominance: float = 0.5
    # P-3：RelationshipModel 快照（重启恢复；普通消息不改变）
    relationship: dict = field(default_factory=dict)
    # 用户睡眠周期（2026-08-30 用户拍板）：user_asleep=用户当前是否在睡
    user_asleep: bool = False
    last_sleep_report_at: str = ""  # 最近一次睡眠/苏醒报告时刻（UTC ISO）
    # 内部
    _last_tick: float = field(default=0.0, repr=False)
    _mood_score: float = field(default=0.0, repr=False)   # 近期互动累积
    _total_messages: int = field(default=0, repr=False)

    def __post_init__(self):
        self._last_tick = time.time()
        if self.attachment == 0.0:
            self.attachment = max(0.0, min(0.95, self.initial_attachment))

    # ---------- R1 状态变更（R1_SPEC 5） ----------

    def apply(self, event: str, delta: dict | None = None, *, cause: str = "") -> None:
        """统一状态变更入口：记录原因 + debug 日志（R1_SPEC 5）。

        event: user_message|assistant_reply|time_decay|scene_change|user_feedback
        delta: 可更新的字段子集（energy/mood/social_appetite/attention_topic/...）
        cause: 人类可读原因（如 "用户提到加班"），默认取 event。
        """
        delta = delta or {}
        self.last_cause = cause or event
        for key, val in delta.items():
            if key == "energy":
                self.energy = max(0.0, min(100.0, float(val)))
            elif key == "social_appetite":
                self.social_appetite = max(0.0, min(1.0, float(val)))
            elif key == "attention_topic":
                self.attention_topic = str(val or "")
            elif key == "attention_scene":
                self.attention_scene = str(val or "normal")
            elif key == "last_interaction_channel":
                self.last_interaction_channel = str(val or "")
            else:
                logger.debug("apply: unknown delta key %r (ignored)", key)
        logger.debug("state changed cause=%s event=%s", self.last_cause, event)

    # ---------- 时间推进 ----------

    def tick(self, decay_per_minute: float = 0.02, social_decay_per_minute: float = 0.005) -> None:
        """按流逝时间衰减精力（调用方定期触发，如每条消息前）。

        R1：social_appetite 同步缓慢衰减（互动恢复，R1_SPEC 5）。
        """
        now = time.time()
        dt_min = (now - self._last_tick) / 60.0
        self._last_tick = now
        self.energy = max(0.0, min(100.0, self.energy - decay_per_minute * dt_min))
        self.social_appetite = max(0.0, min(1.0, self.social_appetite - social_decay_per_minute * dt_min))
        if dt_min > 1.0:
            self.apply("time_decay", cause="时间流逝")

    # ---------- 对话反馈 ----------

    def on_user_message(self, *, positive: bool = True, recover_per_message: float = 3.0, channel: str = "") -> None:
        """用户消息反馈：恢复精力，累积情绪分；R1 恢复社交欲并记录通道。"""
        self.energy = min(100.0, self.energy + recover_per_message)
        self.social_appetite = min(1.0, self.social_appetite + 0.1)
        self._mood_score += 1.0 if positive else -1.0
        self._total_messages += 1
        self._update_mood()
        self._update_attachment()
        if channel:
            self.last_interaction_channel = channel
        self.apply("user_message", cause="收到用户消息")

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

    # ---------- 持久化快照（2026-08-04 重启续接） ----------

    def to_snapshot(self) -> dict:
        """状态 → 可持久化 dict（存 SQLite agent_state 单行）。"""
        return {
            "energy": round(self.energy, 2),
            "mood": self.mood,
            "attachment": round(self.attachment, 4),
            "mood_score": round(self._mood_score, 2),
            "total_messages": self._total_messages,
            # R1 字段（R1_SPEC 5：旧库缺列时 from_snapshot 用 .get 默认）
            "social_appetite": round(self.social_appetite, 3),
            "attention_topic": self.attention_topic,
            "attention_scene": self.attention_scene,
            "last_interaction_channel": self.last_interaction_channel,
            "last_cause": self.last_cause,
            # P-3：PAD + 关系快照（旧库缺字段时 from_snapshot 用默认）
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "relationship": self.relationship,
            # 用户睡眠周期（2026-08-30 用户拍板）
            "user_asleep": int(bool(self.user_asleep)),
            "last_sleep_report_at": self.last_sleep_report_at,
        }

    @classmethod
    def from_snapshot(cls, data: dict, *, initial_attachment: float = 0.5) -> "AgentState":
        """持久化 dict → 状态。缺失字段用默认；_last_tick 重置为当前时间。

        R1：.get() 默认值 → 旧 SQLite 自动兼容（R1_SPEC 5）。
        """
        st = cls(initial_attachment=initial_attachment)
        st.energy = max(0.0, min(100.0, float(data.get("energy", st.energy))))
        mood = data.get("mood", st.mood)
        st.mood = mood if mood in ("开心", "平静", "低落", "期待") else st.mood
        st.attachment = max(0.0, min(0.95, float(data.get("attachment", st.attachment))))
        st._mood_score = float(data.get("mood_score", 0.0))
        st._total_messages = max(0, int(data.get("total_messages", 0)))
        st.social_appetite = max(0.0, min(1.0, float(data.get("social_appetite", st.social_appetite))))
        st.attention_topic = str(data.get("attention_topic", st.attention_topic) or "")
        st.attention_scene = str(data.get("attention_scene", st.attention_scene) or "normal")
        st.last_interaction_channel = str(data.get("last_interaction_channel", st.last_interaction_channel) or "")
        st.last_cause = str(data.get("last_cause", st.last_cause) or "startup")
        # P-3：PAD 恢复（缺失用默认 0.5）；relationship 快照恢复（缺失保留默认空）
        st.valence = max(0.0, min(1.0, float(data.get("valence", st.valence))))
        st.arousal = max(0.0, min(1.0, float(data.get("arousal", st.arousal))))
        st.dominance = max(0.0, min(1.0, float(data.get("dominance", st.dominance))))
        rel = data.get("relationship")
        st.relationship = dict(rel) if isinstance(rel, dict) else {}
        st.user_asleep = bool(data.get("user_asleep", st.user_asleep))
        st.last_sleep_report_at = str(data.get("last_sleep_report_at", st.last_sleep_report_at) or "")
        return st

    @property
    def total_messages(self) -> int:
        return self._total_messages

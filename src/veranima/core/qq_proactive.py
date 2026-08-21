from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum


class QQUserState(StrEnum):
    NORMAL = "normal"
    SLEEPING = "sleeping"
    BUSY = "busy"
    LOW_MOOD = "low_mood"
    AWAY = "away"
    CLOSING = "closing"


@dataclass
class QQProactiveState:
    last_user_message_at: str | None = None
    last_qq_proactive_at: str | None = None
    next_evaluation_at: str | None = None
    user_state: QQUserState = QQUserState.NORMAL
    user_state_started_at: str | None = None
    pending_opportunity: dict | None = None
    routine_profile: dict = field(default_factory=dict)
    social_capital: dict = field(default_factory=dict)
    proactive_paused: bool = False


@dataclass(frozen=True)
class QQReadiness:
    score: float
    time_factor: float
    momentum: float
    routine_multiplier: float
    material_multiplier: float
    social_multiplier: float


@dataclass(frozen=True)
class QQSchedule:
    action: str
    delay_minutes: int


class QQProactiveEngine:
    """QQ-only timing/state engine; it never observes or schedules the pet channel."""

    _STATE_PATTERNS = (
        (QQUserState.SLEEPING, ("我去睡", "去睡了", "晚安", "困了", "先睡", "好累")),
        (QQUserState.LOW_MOOD, ("心情不好", "不想说话", "让我静静", "心情很差")),
        (QQUserState.BUSY, ("开会", "忙去了", "先工作", "回头聊", "赶工")),
        (QQUserState.AWAY, ("出门了", "出去", "车上", "信号不好")),
        (QQUserState.CLOSING, ("拜拜", "好的", "嗯")),
    )

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.config = cfg
        self.sleep_silence_hours = float(cfg.get("sleep_silence_hours", 8))
        self.sleep_min_hours = float(cfg.get("sleep_min_hours", 6))
        self.sleep_max_hours = float(cfg.get("sleep_max_hours", 12))
        self.post_silence_buffer_minutes = int(cfg.get("post_silence_buffer_minutes", 30))
        self.low_activity_multiplier = float(cfg.get("low_activity_multiplier", 0.3))

    @staticmethod
    def _parse(value: str | datetime.datetime) -> datetime.datetime:
        if isinstance(value, datetime.datetime):
            return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)

    @classmethod
    def detect_state(cls, text: str) -> QQUserState:
        text = str(text or "")
        hits = {state for state, patterns in cls._STATE_PATTERNS if any(p in text for p in patterns)}
        for state in (QQUserState.SLEEPING, QQUserState.LOW_MOOD, QQUserState.BUSY, QQUserState.AWAY, QQUserState.CLOSING):
            if state in hits:
                return state
        return QQUserState.NORMAL

    def note_user_message(self, state: QQProactiveState, text: str, *, at: str | None = None) -> None:
        state.last_user_message_at = at or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        detected = self.detect_state(text)
        if any(token in text for token in ("别主动找我", "不要主动找我", "不要打扰", "别打扰我")):
            state.proactive_paused = True
        elif any(token in text for token in ("可以主动找我", "恢复主动", "解除免打扰")):
            state.proactive_paused = False
        if state.user_state != QQUserState.NORMAL and detected == QQUserState.NORMAL:
            state.user_state = QQUserState.NORMAL
            state.user_state_started_at = None
        elif detected != QQUserState.NORMAL:
            state.user_state = detected
            state.user_state_started_at = state.last_user_message_at
        state.pending_opportunity = None

    def time_factor(self, elapsed_hours: float) -> float:
        h = max(0.0, float(elapsed_hours))
        if h <= 2:
            return 0.05 + 0.025 * (h / 2)
        if h <= 8:
            return 0.075 + 0.425 * ((h - 2) / 6) ** 1.35
        if h <= 24:
            return 0.5 + 0.2 * ((h - 8) / 16) ** 0.75
        if h <= 72:
            return 0.7
        return max(0.55, 0.7 - min(0.15, (h - 72) / 168 * 0.15))

    def state_allows_proactive(self, state: QQProactiveState, now) -> bool:
        if state.proactive_paused:
            return False
        if state.user_state in (QQUserState.LOW_MOOD, QQUserState.BUSY, QQUserState.AWAY, QQUserState.CLOSING):
            if not state.user_state_started_at:
                return False
            elapsed = (self._parse(now) - self._parse(state.user_state_started_at)).total_seconds() / 3600
            limits = {
                QQUserState.BUSY: 2,
                QQUserState.AWAY: 1,
                QQUserState.CLOSING: 0.5,
                QQUserState.LOW_MOOD: 24,
            }
            return elapsed >= limits[state.user_state]
        if state.user_state != QQUserState.SLEEPING or not state.user_state_started_at:
            return True
        elapsed = (self._parse(now) - self._parse(state.user_state_started_at)).total_seconds() / 3600
        elapsed = min(elapsed, self.sleep_max_hours)
        return elapsed >= self.sleep_silence_hours + self.post_silence_buffer_minutes / 60

    @staticmethod
    def virtual_state_prefix(elapsed_hours: float) -> str:
        hours = max(0.0, float(elapsed_hours))
        if hours < 2:
            return ""
        if hours < 6:
            return "刚安静下来，"
        if hours < 12:
            return "刚醒过来，"
        if hours < 24:
            return "过了大半天，"
        return "突然想起来，"

    def evaluate(self, state: QQProactiveState, *, now, momentum=1.0,
                 routine_multiplier=1.0, material_multiplier=0.5,
                 social_multiplier=1.0) -> QQReadiness:
        if not state.last_user_message_at:
            elapsed = 72.0
        else:
            elapsed = max(0.0, (self._parse(now) - self._parse(state.last_user_message_at)).total_seconds() / 3600)
        values = [
            self.time_factor(elapsed),
            max(0.5, min(1.5, float(momentum))),
            max(0.3, min(1.2, float(routine_multiplier))),
            max(0.5, min(2.0, float(material_multiplier))),
            max(0.2, min(1.3, float(social_multiplier))),
        ]
        score = max(0.0, min(1.0, math.prod(values)))
        return QQReadiness(score, values[0], values[1], values[2], values[3], values[4])

    @staticmethod
    def schedule(score: float) -> QQSchedule:
        score = float(score)
        if score < 0.30:
            return QQSchedule("reevaluate", 60)
        if score < 0.70:
            return QQSchedule("reevaluate", 15)
        return QQSchedule("generate", 0)

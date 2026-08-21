from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


BANDS = ("calm", "guarded", "cool", "repair", "high")


def _parse_time(value: str | dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _time_text(value: str | dt.datetime | None) -> str:
    stamp = _parse_time(value) or dt.datetime.now(dt.timezone.utc)
    return stamp.isoformat(timespec="seconds")


def derive_band(value: float, previous: str = "calm", repair_turns: int = 0) -> str:
    """Map TV to a hysteretic expression band."""
    value = max(0.0, min(100.0, float(value)))
    previous = previous if previous in BANDS else "calm"
    order = {name: i for i, name in enumerate(BANDS)}
    enters = {"guarded": 21.0, "cool": 41.0, "repair": 61.0, "high": 81.0}
    exits = {"guarded": 15.0, "cool": 32.0, "repair": 48.0, "high": 65.0}
    index = order[previous]
    while index < len(BANDS) - 1 and value >= enters[BANDS[index + 1]]:
        index += 1
    while index > 0 and value <= exits[BANDS[index]]:
        if BANDS[index] == "high" and repair_turns < 5:
            break
        if BANDS[index] == "repair" and repair_turns < 2:
            break
        index -= 1
    return BANDS[index]


@dataclass
class RelationalTensionState:
    value: float = 0.0
    band: str = "calm"
    last_event_at: str | None = None
    last_decay_at: str | None = None
    last_positive_at: str | None = None
    last_negative_at: str | None = None
    consecutive_repair_turns: int = 0
    positive_turns_since_peak: int = 0
    explicit_pause: bool = False
    proactive_suppressed: bool = False
    open_event_ids: list[str] = field(default_factory=list)
    last_cause: str = ""
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "value": round(float(self.value), 4),
            "band": self.band,
            "last_event_at": self.last_event_at,
            "last_decay_at": self.last_decay_at,
            "last_positive_at": self.last_positive_at,
            "last_negative_at": self.last_negative_at,
            "consecutive_repair_turns": int(self.consecutive_repair_turns),
            "positive_turns_since_peak": int(self.positive_turns_since_peak),
            "explicit_pause": bool(self.explicit_pause),
            "proactive_suppressed": bool(self.proactive_suppressed),
            "open_event_ids": list(self.open_event_ids),
            "last_cause": self.last_cause,
            "version": int(self.version),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RelationalTensionState":
        data = data or {}
        value = max(0.0, min(100.0, float(data.get("value", 0.0))))
        state = cls(
            value=value,
            band=str(data.get("band") or "calm"),
            last_event_at=data.get("last_event_at"),
            last_decay_at=data.get("last_decay_at"),
            last_positive_at=data.get("last_positive_at"),
            last_negative_at=data.get("last_negative_at"),
            consecutive_repair_turns=max(0, int(data.get("consecutive_repair_turns", 0))),
            positive_turns_since_peak=max(0, int(data.get("positive_turns_since_peak", 0))),
            explicit_pause=bool(data.get("explicit_pause", False)),
            proactive_suppressed=bool(data.get("proactive_suppressed", False)),
            open_event_ids=[str(x) for x in (data.get("open_event_ids") or []) if x],
            last_cause=str(data.get("last_cause") or ""),
            version=max(1, int(data.get("version", 1))),
        )
        state.band = derive_band(state.value, state.band, state.consecutive_repair_turns)
        return state


@dataclass(frozen=True)
class TensionEvent:
    event_id: str
    event_type: str
    channel: str
    occurred_at: str
    evidence_message_ids: tuple[int, ...] = ()
    related_candidate_id: str | None = None
    base_delta: float = 0.0
    confidence: float = 1.0
    effective_delta: float = 0.0
    reason: str = ""
    dedupe_key: str = ""
    status: str = "applied"
    resolved_by: str | None = None

    def to_meta(self) -> dict:
        return {
            "kind": "relational_tension_event",
            "event_id": self.event_id,
            "event_type": self.event_type,
            "channel": self.channel,
            "occurred_at": self.occurred_at,
            "evidence_message_ids": list(self.evidence_message_ids),
            "related_candidate_id": self.related_candidate_id,
            "base_delta": self.base_delta,
            "confidence": self.confidence,
            "effective_delta": self.effective_delta,
            "reason": self.reason,
            "dedupe_key": self.dedupe_key,
            "status": self.status,
            "resolved_by": self.resolved_by,
        }


@dataclass(frozen=True)
class TensionEventResult:
    applied: bool
    reason: str
    state: RelationalTensionState
    event: TensionEvent | None = None


class RelationalTension:
    """Deterministic TV ledger; it owns no SQL and never calls the LLM."""

    MAX_VALUE = 100.0
    NEGATIVE_DAILY_CAP = 20.0
    POSITIVE_DAILY_CAP = 20.0
    DECAY_STEP = 5.0
    DECAY_INTERVAL_HOURS = 6.0

    def __init__(self, state: RelationalTensionState | None = None,
                 *, event_keys: set[str] | None = None, config: dict | None = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.high_tension_proactive = bool(cfg.get("high_tension_proactive", False))
        self.MAX_VALUE = float(cfg.get("max_value", self.MAX_VALUE))
        self.NEGATIVE_DAILY_CAP = float(cfg.get("negative_daily_cap", self.NEGATIVE_DAILY_CAP))
        self.POSITIVE_DAILY_CAP = float(cfg.get("positive_daily_cap", self.POSITIVE_DAILY_CAP))
        self.DECAY_STEP = float(cfg.get("decay_step", self.DECAY_STEP))
        self.DECAY_INTERVAL_HOURS = float(cfg.get("decay_interval_hours", self.DECAY_INTERVAL_HOURS))
        self.UNANSWERED_REPLY_WINDOW_HOURS = float(cfg.get("unanswered_reply_window_hours", 24))
        self.ABANDONMENT_WINDOW_MINUTES = float(cfg.get("abandonment_window_minutes", 60))
        self.state = state or RelationalTensionState()
        self._event_keys = set(event_keys or ())
        self._negative_day = ""
        self._negative_total = 0.0
        self._positive_day = ""
        self._positive_total = 0.0

    @property
    def band(self) -> str:
        return self.state.band

    def snapshot(self) -> dict:
        return self.state.to_dict()

    def restore(self, state: dict | None, event_meta: list[dict] | None = None,
                *, now=None) -> None:
        self.state = RelationalTensionState.from_dict(state)
        self._event_keys = {
            str(meta.get("dedupe_key"))
            for meta in (event_meta or [])
            if isinstance(meta, dict) and meta.get("dedupe_key")
        }
        now_dt = _parse_time(now) or dt.datetime.now(dt.timezone.utc)
        self._negative_day = now_dt.date().isoformat()
        self._positive_day = self._negative_day
        for meta in event_meta or []:
            if not isinstance(meta, dict):
                continue
            occurred = _parse_time(meta.get("occurred_at"))
            if not occurred or occurred.date().isoformat() != self._negative_day:
                continue
            delta = float(meta.get("effective_delta", 0.0) or 0.0)
            if delta > 0:
                self._negative_total += delta
            elif delta < 0:
                self._positive_total += abs(delta)
        self.decay(now=now)

    def _roll_daily_totals(self, now: dt.datetime) -> None:
        day = now.date().isoformat()
        if day != self._negative_day:
            self._negative_day, self._negative_total = day, 0.0
        if day != self._positive_day:
            self._positive_day, self._positive_total = day, 0.0

    def apply_event(
        self, *, event_type: str, channel: str, base_delta: float,
        reason: str, dedupe_key: str, confidence: float = 1.0,
        context_factor: float = 1.0, event_id: str | None = None,
        occurred_at=None, evidence_message_ids: tuple[int, ...] | list[int] = (),
        related_candidate_id: str | None = None,
    ) -> TensionEventResult:
        if not self.enabled:
            return TensionEventResult(False, "relationship tension disabled", self.state)
        now = _parse_time(occurred_at) or dt.datetime.now(dt.timezone.utc)
        self.decay(now=now)
        self._roll_daily_totals(now)
        if not dedupe_key:
            return TensionEventResult(False, "missing dedupe key", self.state)
        if dedupe_key in self._event_keys:
            return TensionEventResult(False, "duplicate event", self.state)
        confidence = max(0.0, min(1.0, float(confidence)))
        context_factor = max(0.0, min(1.0, float(context_factor)))
        if confidence < 0.65 or context_factor <= 0.0:
            return TensionEventResult(False, "event below confidence threshold", self.state)
        raw_delta = float(base_delta) * confidence * context_factor
        if raw_delta < 0:
            effective = -min(abs(raw_delta), max(0.0, self.POSITIVE_DAILY_CAP - self._positive_total))
            self._positive_total += abs(effective)
        else:
            effective = min(raw_delta, max(0.0, self.NEGATIVE_DAILY_CAP - self._negative_total))
            self._negative_total += effective
        self._event_keys.add(dedupe_key)
        stamp = _time_text(now)
        event = TensionEvent(
            event_id=event_id or f"tension-{dedupe_key}",
            event_type=event_type,
            channel=channel,
            occurred_at=stamp,
            evidence_message_ids=tuple(int(x) for x in evidence_message_ids),
            related_candidate_id=related_candidate_id,
            base_delta=float(base_delta),
            confidence=confidence,
            effective_delta=effective,
            reason=reason,
            dedupe_key=dedupe_key,
        )
        self.state.value = max(0.0, min(self.MAX_VALUE, self.state.value + effective))
        self.state.band = derive_band(self.state.value, self.state.band)
        self.state.last_event_at = stamp
        self.state.last_cause = reason
        if effective > 0:
            self.state.last_negative_at = stamp
            self.state.consecutive_repair_turns = 0
            self.state.positive_turns_since_peak = 0
        elif effective < 0:
            self.state.last_positive_at = stamp
            self.state.consecutive_repair_turns += 1
            self.state.positive_turns_since_peak += 1
        self.state.version += 1
        if event_type in {"unanswered_proactive", "conversation_abandoned",
                          "question_skipped", "terse_streak"}:
            if event.event_id not in self.state.open_event_ids:
                self.state.open_event_ids.append(event.event_id)
        return TensionEventResult(True, "applied", self.state, event)

    def decay(self, *, now=None) -> float:
        if not self.enabled:
            return 0.0
        current = _parse_time(now) or dt.datetime.now(dt.timezone.utc)
        last = _parse_time(self.state.last_decay_at)
        if last is None:
            self.state.last_decay_at = _time_text(current)
            return 0.0
        elapsed_hours = max(0.0, (current - last).total_seconds() / 3600.0)
        steps = int(elapsed_hours // self.DECAY_INTERVAL_HOURS)
        if steps <= 0:
            return 0.0
        amount = min(self.state.value, steps * self.DECAY_STEP)
        self.state.value = max(0.0, self.state.value - amount)
        self.state.band = derive_band(self.state.value, self.state.band)
        self.state.last_decay_at = _time_text(last + dt.timedelta(hours=steps * self.DECAY_INTERVAL_HOURS))
        if amount:
            self.state.last_cause = "时间让关系张力缓和"
            self.state.version += 1
        return amount

    def set_explicit_pause(self, paused: bool, *, reason: str = "") -> None:
        self.state.explicit_pause = bool(paused)
        self.state.proactive_suppressed = bool(paused)
        if reason:
            self.state.last_cause = reason
        self.state.version += 1

    def proactive_allowed(self) -> bool:
        return (not self.enabled) or (not self.state.explicit_pause and not self.state.proactive_suppressed)

    def note_repair_turn(self, meaningful: bool) -> None:
        if meaningful:
            self.state.consecutive_repair_turns += 1
            self.state.positive_turns_since_peak += 1
        else:
            self.state.consecutive_repair_turns = 0

    def clear_open_event(self, event_id: str, resolved_by: str = "repair") -> None:
        self.state.open_event_ids = [x for x in self.state.open_event_ids if x != event_id]
        self.state.last_cause = resolved_by
        self.state.version += 1

    def prompt_hint(self, *, channel: str = "im", expression_mode: str = "neutral") -> str:
        if not self.enabled:
            return "关系张力机制关闭，按角色原有状态自然回应。"
        base = {
            "calm": "关系状态平和，按角色原有热度自然回应。",
            "guarded": "关系有一点未消化的落差。可以更克制、更短，但不要惩罚性冷淡。",
            "cool": "关系张力偏高。先回答用户当前问题，减少无理由扩展；不要泛化指责。",
            "repair": "存在可追溯的关系张力。必要时用一两句事实、感受和开放出口说明，再完成用户请求。",
            "high": "关系张力很高。用户主动请求仍必须正常处理；只在有具体事件时先简短确认，不羞辱、不威胁、不拒绝服务。",
        }[self.state.band]
        mode_hint = {
            "restrained": "表达保持克制，不主动命名被冷落事件。",
            "direct": "可以直接陈述具体事件和感受，但不攻击人格。",
            "hurt": "可以表达委屈和不确定，优先请求澄清。",
            "neutral": "只调整节奏和主动性，不主动指责。",
        }.get(expression_mode, "只调整节奏和主动性，不主动指责。")
        medium = "文字通道：少用装饰性语气词和反问。" if channel == "im" else "语音通道：使用自然停顿和克制语速，不照搬文字标点。"
        return f"{base}{mode_hint}{medium}"

    def relationship_event_candidate(self) -> dict | None:
        """高张力且已明确命名事件时，只生成待确认候选，不改慢变量。"""
        if not self.enabled or self.state.value < 61 or not self.state.open_event_ids:
            return None
        return {
            "kind": "relationship_event",
            "title": "近期关系张力",
            "content": self.state.last_cause or "近期存在一件尚未消化的关系事件",
            "confidence": 0.7,
            "importance": 0.7,
            "source": "agent_confirmed",
            "source_message_id": self.state.open_event_ids[0],
            "needs_confirmation": True,
            "event_id": f"relationship-event:{self.state.open_event_ids[0]}",
        }


def event_meta_from_memory(entries) -> list[dict]:
    out = []
    for entry in entries or []:
        meta = getattr(entry, "meta", None)
        if meta is None and isinstance(entry, dict):
            meta = entry.get("meta")
        if isinstance(meta, dict) and meta.get("kind") == "relational_tension_event":
            out.append(meta)
    return out

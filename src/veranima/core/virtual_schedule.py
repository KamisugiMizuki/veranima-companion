"""角色目录中的虚拟日程模板加载与边界校验。"""
from __future__ import annotations

import json
import hashlib
import datetime as dt
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from pathlib import Path


class ScheduleTemplateError(ValueError):
    """日程模板不满足运行时契约。"""


_ALLOWED_CATEGORIES = {
    "obligation", "self_care", "transition", "personal_interest",
    "social", "rest", "sleep_window", "role_defined",
}
_ALLOWED_IMPACTS = {"none", "mild", "inconvenient", "unavailable"}
_ALLOWED_SHARE_POLICIES = {"never", "low_pressure", "normal", "high_value"}
_ALLOWED_CHRONOTYPES = {"day_aligned", "evening_aligned", "night_aligned", "irregular"}
_ALLOWED_PROFILES = {"short_precise", "normal", "drowsy", "fragmented"}


def _local_time(day: dt.date, value: str, zone: ZoneInfo) -> dt.datetime:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ScheduleTemplateError(f"invalid local time: {value}") from exc
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=zone)


@dataclass(frozen=True)
class Circadian:
    wake_start: str
    wake_end: str
    sleep_start: str
    sleep_end: str
    chronotype: str
    recovery_rate_minutes_per_day: int
    target_sleep_minutes: int


@dataclass(frozen=True)
class ScheduleBlock:
    id: str
    category: str
    activity_pool: tuple[str, ...]
    window_start: str
    window_end: str
    duration_min: int
    duration_max: int
    required: bool
    share_policy: str
    interaction_profile: str
    interaction_impact: str
    deviation_policy: dict
    priority: int = 0


@dataclass(frozen=True)
class ScheduleItem:
    id: str
    rule_id: str
    activity_key: str
    category: str
    planned_start: dt.datetime
    planned_end: dt.datetime
    interaction_profile: str
    interaction_impact: str
    share_policy: str
    required: bool


@dataclass(frozen=True)
class ScheduleContext:
    plan_id: str
    item_id: str | None
    activity_category: str
    phase: str
    progress: float
    interaction_profile: str
    availability: float
    reply_budget: dict
    share_allowed: bool
    curiosity_allowed: bool
    source_anchor: dict
    activity_key: str = ""


@dataclass(frozen=True)
class ScheduleRuntimeState:
    state: str = "awake"
    sleep_cycle_id: str = ""
    sleep_started_at: dt.datetime | None = None
    grace_deadline: dt.datetime | None = None
    sleep_debt_minutes: int = 0
    sleep_extension_minutes: int = 0
    sleep_reason: str = ""


class ScheduleRuntime:
    """Small in-memory lifecycle facade; persistence is added with plan storage."""

    def __init__(self, outline: "ScheduleOutline", planner=None) -> None:
        self.outline = outline
        self.state = ScheduleRuntimeState()
        self.planner = planner
        self._next_day_plan: DayPlan | None = None
        self.pending_notice: str = ""

    @property
    def sleeping(self) -> bool:
        return self.state.state == "sleeping"

    def to_snapshot(self) -> dict:
        data = {
            "role_id": self.outline.role_id,
            "state": self.state.state,
            "sleep_cycle_id": self.state.sleep_cycle_id,
            "sleep_started_at": self.state.sleep_started_at.isoformat() if self.state.sleep_started_at else None,
            "grace_deadline": self.state.grace_deadline.isoformat() if self.state.grace_deadline else None,
            "sleep_debt_minutes": self.state.sleep_debt_minutes,
            "sleep_extension_minutes": self.state.sleep_extension_minutes,
            "sleep_reason": self.state.sleep_reason,
        }
        if self._next_day_plan is not None:
            data["next_plan_date"] = self._next_day_plan.local_date.isoformat()
            data["next_plan_source"] = self._next_day_plan.source
        return data

    @classmethod
    def from_snapshot(cls, outline: "ScheduleOutline", snapshot: dict, planner=None):
        runtime = cls(outline, planner=planner)
        if str(snapshot.get("role_id") or "") != outline.role_id:
            return runtime
        parse = lambda value: dt.datetime.fromisoformat(value) if value else None
        runtime.state = ScheduleRuntimeState(
            state=str(snapshot.get("state") or "awake"),
            sleep_cycle_id=str(snapshot.get("sleep_cycle_id") or ""),
            sleep_started_at=parse(snapshot.get("sleep_started_at")),
            grace_deadline=parse(snapshot.get("grace_deadline")),
            sleep_debt_minutes=max(0, int(snapshot.get("sleep_debt_minutes", 0))),
            sleep_extension_minutes=max(0, int(snapshot.get("sleep_extension_minutes", 0))),
            sleep_reason=str(snapshot.get("sleep_reason") or ""),
        )
        next_date = snapshot.get("next_plan_date")
        if next_date:
            try:
                day = dt.date.fromisoformat(str(next_date))
                runtime._next_day_plan = outline.build_day_plan(
                    dt.datetime.combine(day, dt.time(), tzinfo=ZoneInfo(outline.timezone)),
                    source=str(snapshot.get("next_plan_source") or "deterministic_fallback"),
                )
            except (TypeError, ValueError, ScheduleTemplateError):
                runtime._next_day_plan = None
        return runtime

    def generate_next_day(self, when: dt.datetime, llm_output: dict | None = None) -> DayPlan:
        candidate = llm_output or {}
        profile_id = str(candidate.get("day_profile") or self.outline.default_day_profile)
        profile = self.outline.day_profiles.get(profile_id)
        allowed = set((profile or {}).get("allowed_block_ids") or ())
        raw_items = candidate.get("items")
        valid = isinstance(raw_items, list) and bool(raw_items) and all(
            isinstance(item, dict)
            and item.get("rule_id") in allowed
            and item.get("activity_key") in next(
                block.activity_pool for block in self.outline.blocks if block.id == item.get("rule_id")
            )
            for item in raw_items
        )
        # Invalid structured output never mutates the plan; deterministic fallback wins.
        plan = self.outline.build_day_plan(when)
        if plan is None:
            raise ScheduleTemplateError("cannot generate a plan from a disabled outline")
        if not valid:
            return DayPlan(plan.plan_id, plan.role_id, plan.local_date, plan.timezone, plan.items,
                           plan.interaction_profiles, source="deterministic_fallback")
        try:
            return self.outline.build_day_plan(
                when,
                day_profile=profile_id,
                adjustments=candidate.get("items", []),
                source="llm_structured_template",
            ) or plan
        except (TypeError, ValueError, ScheduleTemplateError):
            return DayPlan(plan.plan_id, plan.role_id, plan.local_date, plan.timezone, plan.items,
                           plan.interaction_profiles, source="deterministic_fallback")

    def begin_sleep_preparation(self, when: dt.datetime) -> ScheduleRuntimeState:
        if self.state.state == "sleeping":
            return self.state
        sleep = getattr(self.outline, "sleep", {}) or {}
        grace = max(0, min(60, int(sleep.get("grace_period_minutes", 30))))
        self.state = ScheduleRuntimeState(
            state="sleep_preparing", sleep_cycle_id=f"{self.outline.role_id}:{when.date().isoformat()}",
            sleep_started_at=when, grace_deadline=when + dt.timedelta(minutes=grace),
            sleep_debt_minutes=self.state.sleep_debt_minutes,
        )
        self.pending_notice = "sleep_preparing"
        return self.state

    def extend_wakefulness(self, when: dt.datetime) -> ScheduleRuntimeState:
        if self.state.state == "awake":
            return self.begin_sleep_preparation(when)
        if self.state.state == "sleeping":
            return self.state
        deadline = self.state.grace_deadline
        if deadline is None or when >= deadline:
            extension = max(
                self.state.sleep_extension_minutes,
                int((when - deadline).total_seconds() // 60) if deadline else 0,
            )
            self.state = ScheduleRuntimeState(
                state="sleeping", sleep_cycle_id=self.state.sleep_cycle_id,
                sleep_started_at=self.state.sleep_started_at, grace_deadline=deadline,
                sleep_debt_minutes=max(1, self.state.sleep_debt_minutes + extension),
                sleep_extension_minutes=extension,
                sleep_reason="late_sleep" if extension else "scheduled_sleep",
            )
        else:
            started = self.state.sleep_started_at or when
            sleep_cfg = getattr(self.outline, "sleep", {}) or {}
            max_extension = max(0, min(60, int(sleep_cfg.get("max_extension_minutes", 30))))
            hard_deadline = started + dt.timedelta(
                minutes=max(0, int((deadline - started).total_seconds() // 60)) + max_extension
            )
            extended_deadline = min(hard_deadline, when + dt.timedelta(minutes=max_extension))
            extension = max(0, int((extended_deadline - (deadline or started)).total_seconds() // 60))
            if extended_deadline <= when:
                return self.extend_wakefulness(extended_deadline)
            self.state = ScheduleRuntimeState(
                state="sleep_preparing", sleep_cycle_id=self.state.sleep_cycle_id,
                sleep_started_at=self.state.sleep_started_at, grace_deadline=extended_deadline,
                sleep_debt_minutes=self.state.sleep_debt_minutes,
                sleep_extension_minutes=extension,
                sleep_reason="late_sleep" if extension else "",
            )
        return self.state

    def advance(self, when: dt.datetime) -> ScheduleRuntimeState:
        if self.state.state == "sleeping" and self.state.sleep_started_at and self.outline.circadian:
            wake_at = self.state.sleep_started_at + dt.timedelta(minutes=self.outline.circadian.target_sleep_minutes)
            if when >= wake_at:
                previous_debt = self.state.sleep_debt_minutes
                self._next_day_plan = None
                self.state = ScheduleRuntimeState(
                    state="awake",
                    sleep_cycle_id=f"{self.outline.role_id}:{when.date().isoformat()}:awake",
                    sleep_debt_minutes=max(
                        0, previous_debt - self.outline.circadian.recovery_rate_minutes_per_day
                    ),
                    sleep_reason="woke",
                )
                self.pending_notice = "woke"
        plan = self._next_day_plan or self.outline.build_day_plan(when)
        if plan:
            context = plan.context_at(when)
            if context.activity_category == "sleep_window" and self.state.state == "awake":
                self.begin_sleep_preparation(when)
        if self.state.state == "sleep_preparing":
            deadline = self.state.grace_deadline
            if deadline is not None and when >= deadline:
                self._force_sleep(when)
        if self.state.state == "sleeping" and self._next_day_plan is None:
            self.generate_next_day_after_sleep(when)
        return self.state

    def _force_sleep(self, when: dt.datetime) -> ScheduleRuntimeState:
        extension = max(0, self.state.sleep_extension_minutes)
        self.state = ScheduleRuntimeState(
            state="sleeping", sleep_cycle_id=self.state.sleep_cycle_id,
            sleep_started_at=self.state.sleep_started_at, grace_deadline=self.state.grace_deadline,
            sleep_debt_minutes=max(1, self.state.sleep_debt_minutes + extension),
            sleep_extension_minutes=extension,
            sleep_reason="late_sleep" if extension else "scheduled_sleep",
        )
        return self.state

    def current_context(self, when: dt.datetime) -> ScheduleContext:
        if self.state.state == "sleeping":
            return ScheduleContext("", None, "sleep_window", "sleep_like", 1.0, "sleep_like", 0.0, {}, False, False, {"truth_class": "virtual_simulation"})
        plan = self._next_day_plan or self.outline.build_day_plan(when)
        if plan is None:
            return ScheduleContext("", None, "gap", "gap", 0.0, "available_normal", 1.0, {}, True, True, {})
        return plan.context_at(when)

    def pop_notice(self) -> str:
        value, self.pending_notice = self.pending_notice, ""
        return value

    def generate_next_day_after_sleep(self, when: dt.datetime) -> DayPlan:
        if self.state.state != "sleeping":
            raise ScheduleTemplateError("next-day plan requires sleeping state")
        if self._next_day_plan is not None:
            return self._next_day_plan
        output = self.planner(when) if self.planner else None
        self._next_day_plan = self.generate_next_day(when + dt.timedelta(days=1), output)
        return self._next_day_plan


@dataclass(frozen=True)
class DayPlan:
    plan_id: str
    role_id: str
    local_date: dt.date
    timezone: str
    items: tuple[ScheduleItem, ...]
    interaction_profiles: dict
    source: str = "deterministic_template"

    def context_at(self, when: dt.datetime) -> ScheduleContext:
        zone = ZoneInfo(self.timezone)
        current = when.astimezone(zone) if when.tzinfo else when.replace(tzinfo=zone)
        for item in self.items:
            if item.planned_start <= current < item.planned_end:
                duration = (item.planned_end - item.planned_start).total_seconds()
                progress = max(0.0, min(1.0, (current - item.planned_start).total_seconds() / duration))
                profile = dict(self.interaction_profiles.get(item.interaction_profile) or {})
                # The template remains the source of interaction constraints.
                return ScheduleContext(
                    self.plan_id, item.id, item.category, "active", progress,
                    item.interaction_profile, 0.35 if item.interaction_impact != "none" else 0.8,
                    profile, item.share_policy != "never", item.interaction_impact == "none",
                    {"truth_class": "virtual_simulation", "plan_id": self.plan_id, "item_id": item.id},
                    item.activity_key,
                )
        return ScheduleContext(
            self.plan_id, None, "gap", "gap", 0.0, "available_normal", 1.0,
            {}, True, True, {"truth_class": "virtual_simulation", "plan_id": self.plan_id},
        )


@dataclass(frozen=True)
class ScheduleOutline:
    role_id: str
    enabled: bool
    schema_version: int
    timezone: str
    default_day_profile: str
    day_profiles: dict
    blocks: tuple[ScheduleBlock, ...]
    circadian: Circadian | None
    interaction_profiles: dict
    autonomy: dict
    sleep: dict
    template_path: Path | None = None

    @classmethod
    def from_role_dir(cls, role_dir: str | Path) -> "ScheduleOutline":
        role_dir = Path(role_dir).resolve()
        path = role_dir / "virtual_schedule.json"
        role_id = role_dir.name
        if not path.is_file():
            return cls(role_id, False, 0, "", "", {}, (), None, {}, {}, {}, None)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScheduleTemplateError(f"cannot load virtual_schedule.json: {exc}") from exc
        if not isinstance(raw, dict):
            raise ScheduleTemplateError("template root must be an object")
        return cls._from_dict(role_id, path, raw)

    @classmethod
    def _from_dict(cls, role_id: str, path: Path, raw: dict) -> "ScheduleOutline":
        if raw.get("schema_version") != 1:
            raise ScheduleTemplateError("schema_version must be 1")
        enabled = raw.get("enabled") is True
        timezone = raw.get("timezone")
        default_profile = raw.get("default_day_profile")
        profiles = raw.get("day_profiles")
        blocks_raw = raw.get("blocks")
        if not isinstance(timezone, str) or not timezone.strip():
            raise ScheduleTemplateError("timezone must be a non-empty string")
        if not isinstance(default_profile, str) or not isinstance(profiles, dict) or default_profile not in profiles:
            raise ScheduleTemplateError("default_day_profile must reference day_profiles")
        if not isinstance(blocks_raw, list):
            raise ScheduleTemplateError("blocks must be a list")
        blocks = tuple(cls._block(value) for value in blocks_raw)
        ids = {block.id for block in blocks}
        if len(ids) != len(blocks):
            raise ScheduleTemplateError("block ids must be unique")
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or not isinstance(profile.get("allowed_block_ids"), list):
                raise ScheduleTemplateError(f"day_profiles.{profile_id}.allowed_block_ids must be a list")
            unknown = set(profile["allowed_block_ids"]) - ids
            if unknown:
                raise ScheduleTemplateError(f"allowed_block_ids references unknown blocks: {sorted(unknown)}")
        circadian = cls._circadian(raw.get("circadian"))
        interaction_profiles = raw.get("interaction_profiles") or {}
        if not isinstance(interaction_profiles, dict):
            raise ScheduleTemplateError("interaction_profiles must be an object")
        for profile_id, profile in interaction_profiles.items():
            if not isinstance(profile, dict):
                raise ScheduleTemplateError(f"interaction profile {profile_id} must be an object")
            reply_style = profile.get("reply_style", "normal")
            if reply_style not in _ALLOWED_PROFILES:
                raise ScheduleTemplateError(f"interaction profile {profile_id}.reply_style is invalid")
        autonomy = raw.get("autonomy") or {}
        if not isinstance(autonomy, dict):
            raise ScheduleTemplateError("autonomy must be an object")
        sleep = raw.get("sleep") or {}
        if not isinstance(sleep, dict):
            raise ScheduleTemplateError("sleep must be an object")
        for key in ("grace_period_minutes", "max_extension_minutes"):
            if key in sleep:
                try:
                    value = int(sleep[key])
                except (TypeError, ValueError) as exc:
                    raise ScheduleTemplateError(f"sleep.{key} is invalid") from exc
                if not 0 <= value <= 60:
                    raise ScheduleTemplateError(f"sleep.{key} must be between 0 and 60")
        return cls(role_id, enabled, 1, timezone, default_profile, profiles, blocks,
                   circadian, interaction_profiles, autonomy, sleep, path)

    def build_day_plan(self, when: dt.datetime, *, day_profile: str | None = None,
                       revision: int = 1, adjustments: list[dict] | None = None,
                       source: str = "deterministic_template") -> DayPlan | None:
        if not self.enabled:
            return None
        zone = ZoneInfo(self.timezone)
        local = when.astimezone(zone) if when.tzinfo else when.replace(tzinfo=zone)
        local_date = local.date()
        profile_id = day_profile or self.default_day_profile
        profile = self.day_profiles.get(profile_id)
        if not isinstance(profile, dict):
            raise ScheduleTemplateError(f"unknown day profile: {profile_id}")
        allowed = set(profile["allowed_block_ids"])
        seed_text = f"{self.role_id}|{local_date.isoformat()}|{revision}|{profile_id}|{self.schema_version}"
        plan_id = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:24]
        items = []
        adjustment_map = {item.get("rule_id"): item for item in (adjustments or []) if isinstance(item, dict)}
        for sequence, block in enumerate(sorted((b for b in self.blocks if b.id in allowed), key=lambda b: (b.window_start, b.priority, b.id))):
            start_day = local_date
            start = _local_time(start_day, block.window_start, zone)
            end_window = _local_time(start_day, block.window_end, zone)
            if end_window <= start:
                end_window += dt.timedelta(days=1)
            if block.category == "sleep_window" and block.window_end <= block.window_start:
                start_day = local_date
                start = _local_time(start_day, block.window_start, zone)
                end_window = _local_time(start_day + dt.timedelta(days=1), block.window_end, zone)
            end = start + dt.timedelta(minutes=block.duration_min)
            if end > end_window:
                end = end_window
            if end <= start:
                raise ScheduleTemplateError(f"block {block.id} cannot fit its preferred window")
            adjustment = adjustment_map.get(block.id) or {}
            operation = adjustment.get("operation", "none")
            shift = int(adjustment.get("shift_minutes", 0) or 0)
            duration = int(adjustment.get("duration_minutes", (end - start).total_seconds() // 60))
            if operation in {"shift", "resize", "substitute", "recovery_mode", "none"}:
                if operation != "resize":
                    start += dt.timedelta(minutes=shift)
                if operation in {"resize", "recovery_mode"}:
                    duration = max(block.duration_min, min(block.duration_max, duration))
                end = start + dt.timedelta(minutes=duration)
            elif operation == "skip_optional" and not block.required:
                continue
            elif operation == "skip_optional":
                raise ScheduleTemplateError(f"required block {block.id} cannot be skipped")
            else:
                raise ScheduleTemplateError(f"unsupported adjustment operation: {operation}")
            if start < _local_time(local_date, block.window_start, zone) or end > end_window:
                raise ScheduleTemplateError(f"adjustment for block {block.id} leaves preferred window")
            activity_key = str(adjustment.get("activity_key") or block.activity_pool[0])
            if activity_key not in block.activity_pool:
                raise ScheduleTemplateError(f"activity for block {block.id} is not allowed")
            items.append(ScheduleItem(
                id=f"{plan_id}:{sequence}:{block.id}", rule_id=block.id,
                activity_key=activity_key, category=block.category,
                planned_start=start, planned_end=end,
                interaction_profile=block.interaction_profile,
                interaction_impact=block.interaction_impact,
                share_policy=block.share_policy, required=block.required,
            ))
        for previous, current in zip(items, items[1:]):
            if current.planned_start < previous.planned_end:
                raise ScheduleTemplateError("generated schedule items overlap")
        return DayPlan(plan_id, self.role_id, local_date, self.timezone, tuple(items), dict(self.interaction_profiles), source=source)

    @staticmethod
    def _block(raw) -> ScheduleBlock:
        if not isinstance(raw, dict):
            raise ScheduleTemplateError("each block must be an object")
        required = ("id", "category", "activity_pool", "preferred_window", "duration_minutes",
                    "share_policy", "interaction_profile", "interaction_impact", "deviation_policy")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ScheduleTemplateError(f"block missing fields: {', '.join(missing)}")
        block_id = raw["id"]
        if not isinstance(block_id, str) or not block_id.strip():
            raise ScheduleTemplateError("block.id must be a non-empty string")
        category = raw["category"]
        if category not in _ALLOWED_CATEGORIES:
            raise ScheduleTemplateError(f"block {block_id}.category is invalid")
        pool = raw["activity_pool"]
        if not isinstance(pool, list) or not pool or any(not isinstance(value, str) or not value.strip() for value in pool):
            raise ScheduleTemplateError(f"block {block_id}.activity_pool is invalid")
        window = raw["preferred_window"]
        duration = raw["duration_minutes"]
        if not isinstance(window, dict) or not all(isinstance(window.get(key), str) for key in ("start", "end")):
            raise ScheduleTemplateError(f"block {block_id}.preferred_window is invalid")
        if not isinstance(duration, dict):
            raise ScheduleTemplateError(f"block {block_id}.duration_minutes is invalid")
        try:
            duration_min, duration_max = int(duration["min"]), int(duration["max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ScheduleTemplateError(f"block {block_id}.duration_minutes is invalid") from exc
        if duration_min <= 0 or duration_max < duration_min:
            raise ScheduleTemplateError(f"block {block_id}.duration_minutes range is invalid")
        impact = raw["interaction_impact"]
        if impact not in _ALLOWED_IMPACTS:
            raise ScheduleTemplateError(f"block {block_id}.interaction_impact is invalid")
        share = raw["share_policy"]
        if share not in _ALLOWED_SHARE_POLICIES:
            raise ScheduleTemplateError(f"block {block_id}.share_policy is invalid")
        deviation = raw["deviation_policy"]
        if not isinstance(deviation, dict):
            raise ScheduleTemplateError(f"block {block_id}.deviation_policy is invalid")
        priority = raw.get("priority", 0)
        if not isinstance(priority, int):
            raise ScheduleTemplateError(f"block {block_id}.priority is invalid")
        return ScheduleBlock(
            id=block_id, category=category, activity_pool=tuple(pool),
            window_start=window["start"], window_end=window["end"],
            duration_min=duration_min, duration_max=duration_max,
            required=bool(raw.get("required", False)), share_policy=share,
            interaction_profile=str(raw["interaction_profile"]), interaction_impact=impact,
            deviation_policy=dict(deviation), priority=priority,
        )

    @staticmethod
    def _circadian(raw) -> Circadian | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ScheduleTemplateError("circadian must be an object")
        wake, sleep = raw.get("wake_window"), raw.get("sleep_window")
        if not isinstance(wake, dict) or not isinstance(sleep, dict):
            raise ScheduleTemplateError("circadian windows are required")
        chronotype = raw.get("chronotype")
        if chronotype not in _ALLOWED_CHRONOTYPES:
            raise ScheduleTemplateError("circadian.chronotype is invalid")
        try:
            recovery = int(raw.get("recovery_rate_minutes_per_day", 0))
            target = int(raw["target_sleep_minutes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ScheduleTemplateError("circadian sleep values are invalid") from exc
        if recovery < 0 or not 1 <= target <= 1440:
            raise ScheduleTemplateError("circadian sleep values are out of range")
        if not all(isinstance(value, str) for value in (wake.get("start"), wake.get("end"), sleep.get("start"), sleep.get("end"))):
            raise ScheduleTemplateError("circadian windows are invalid")
        return Circadian(wake["start"], wake["end"], sleep["start"], sleep["end"], chronotype, recovery, target)

from __future__ import annotations

import datetime as dt
import json

from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class Embed:
    dim = 8
    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class LLM:
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs): return '{"segments":[{"text":"ok"}]}'


def test_schedule_runtime_sleep_message_archive_is_metadata_only(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    message_id = memory.store_message("user", "私密正文", channel="qq")

    archive_id = memory.archive_sleep_message(
        role_id="zima", user_scope="qq:100", sleep_cycle_id="cycle-1",
        message_id=message_id, sender_scope="qq:100",
    )

    rows = memory.sleep_messages("zima", "qq:100", "cycle-1")
    assert archive_id > 0
    assert rows[0]["message_id"] == message_id
    assert rows[0]["content_retained"] == 0
    assert "私密正文" not in json.dumps(rows, ensure_ascii=False)


def test_sleep_debt_distinguishes_late_sleep_from_insomnia(tmp_path):
    role_dir = tmp_path / "characters" / "debt"
    role_dir.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480, "recovery_rate_minutes_per_day": 30},
        "sleep": {"grace_period_minutes": 30, "max_extension_minutes": 30, "extension_cost": 1.0},
    }
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))

    runtime.begin_sleep_preparation(dt.datetime(2026, 8, 28, 22, tzinfo=dt.timezone.utc))
    state = runtime.extend_wakefulness(dt.datetime(2026, 8, 28, 22, 31, tzinfo=dt.timezone.utc))

    assert state.state == "sleeping"
    assert state.sleep_debt_minutes == 1
    assert state.sleep_reason == "late_sleep"


def test_sleep_metadata_can_be_reconciled_after_restart(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    message_id = memory.store_message("user", "消息", channel="qq")
    memory.archive_sleep_message(
        role_id="yuki", user_scope="qq:100", sleep_cycle_id="cycle-2",
        message_id=message_id, sender_scope="qq:100",
    )

    reopened = MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed())

    assert reopened.sleep_messages("yuki", "qq:100", "cycle-2")[0]["message_id"] == message_id


def test_schedule_runtime_snapshot_restores_sleep_boundary(tmp_path):
    role_dir = tmp_path / "characters" / "restore"
    role_dir.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {"grace_period_minutes": 30, "max_extension_minutes": 30},
    }
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    first = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))
    start = dt.datetime(2026, 8, 28, 22, tzinfo=dt.timezone.utc)
    first.begin_sleep_preparation(start)
    first.extend_wakefulness(start + dt.timedelta(minutes=31))

    restored = ScheduleRuntime.from_snapshot(first.outline, first.to_snapshot())

    assert restored.state.state == "sleeping"
    assert restored.state.sleep_cycle_id == first.state.sleep_cycle_id
    assert restored.state.sleep_debt_minutes == first.state.sleep_debt_minutes


def test_agent_persists_schedule_runtime_snapshot(tmp_path):
    role_dir = tmp_path / "characters" / "agent-restore"
    role_dir.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {"grace_period_minutes": 0, "max_extension_minutes": 0},
    }
    (role_dir / "character.json").write_text("{}", encoding="utf-8")
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    memory = MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(CharacterCard(name="Restore"), memory, LLM(), AgentState(), config={"root": str(tmp_path), "character_card": str(role_dir / "character.json")})
    agent.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))
    now = dt.datetime(2026, 8, 28, 22, tzinfo=dt.timezone.utc)
    agent.schedule_runtime.begin_sleep_preparation(now)
    agent.schedule_runtime.extend_wakefulness(now)
    agent._persist_state()

    restored = Agent(CharacterCard(name="Restore"), memory, LLM(), None, config={"root": str(tmp_path), "character_card": str(role_dir / "character.json")})

    assert restored.schedule_runtime is not None
    assert restored.schedule_runtime.state.state == "sleeping"


def test_schedule_snapshot_is_not_restored_into_another_role(tmp_path):
    zima_dir = tmp_path / "characters" / "zima"
    yuki_dir = tmp_path / "characters" / "yuki"
    for role_dir in (zima_dir, yuki_dir):
        role_dir.mkdir(parents=True)
        (role_dir / "character.json").write_text("{}", encoding="utf-8")
        value = {
            "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
            "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
            "blocks": [], "interaction_profiles": {}, "autonomy": {},
            "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480},
            "sleep": {"grace_period_minutes": 0, "max_extension_minutes": 0},
        }
        (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    memory = MemoryStore(db_path=str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    zima = Agent(CharacterCard(name="Zima"), memory, LLM(), AgentState(), config={"root": str(tmp_path), "character_card": str(zima_dir / "character.json")})
    now = dt.datetime(2026, 8, 28, 22, tzinfo=dt.timezone.utc)
    zima.schedule_runtime.begin_sleep_preparation(now)
    zima.schedule_runtime.extend_wakefulness(now)
    zima._persist_state()

    yuki = Agent(CharacterCard(name="Yuki"), memory, LLM(), None, config={"root": str(tmp_path), "character_card": str(yuki_dir / "character.json")})

    assert yuki.schedule_runtime.state.state == "awake"


def test_schedule_offset_history_persists_and_recovers_gradually(tmp_path):
    role_dir = tmp_path / "characters" / "offset"
    role_dir.mkdir(parents=True)
    value = {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"}, "sleep_window": {"start": "22:00", "end": "23:00"}, "chronotype": "day_aligned", "target_sleep_minutes": 480, "recovery_rate_minutes_per_day": 20},
        "sleep": {},
    }
    (role_dir / "virtual_schedule.json").write_text(json.dumps(value), encoding="utf-8")
    runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role_dir))

    runtime.apply_offset(90, "late_sleep", dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc))
    runtime.recover_offset(dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc))
    restored = ScheduleRuntime.from_snapshot(runtime.outline, runtime.to_snapshot())

    assert restored.schedule_offset_minutes == 70
    assert [item["offset_minutes"] for item in restored.offset_history] == [90, 70]
    assert restored.offset_history[0]["reason"] == "late_sleep"

"""MIND_LOOP M4（牵挂写明日程）+ 离线补账 2026-09-09 行为验收。

真机 09-08 导出件实锤三条根因（全部用真卡 characters/xumian 复现）：
1. schedule_offset_minutes=100 整体偏移把 sleep 块推出窗口 → build_day_plan 抛
   ScheduleTemplateError 沿 advance() 上抛 → 整条 tick 崩、next_plan_* 恒缺席；
2. 计划生成只活在「state==sleeping」里，安卓夜间杀进程 → 醒来那刻状态已翻回
   awake → 计划与夜眠消化（M3）双双永久关闭；
3. 明日计划口径 when+1day 对凌晨睡的角色差一天 → 醒来当天全落在 gap。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime, ScheduleRuntimeState
from veranima.memory.store import MemoryStore

CARD = Path(__file__).resolve().parents[1] / "characters" / "xumian"
UTC = dt.timezone.utc
CST = dt.timezone(dt.timedelta(hours=8))


def _local(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CST).astimezone(UTC)


def _runtime(offset=0, planner=None):
    rt = ScheduleRuntime(ScheduleOutline.from_role_dir(CARD), planner=planner)
    rt.schedule_offset_minutes = offset
    return rt


def test_offset_no_longer_kills_advance_and_anchors_stay():
    """偏移装不下就整体不偏移：xumian 的 sleep 填满窗口 → 计划照常落盘、全按模板。"""
    rt = _runtime(offset=100)
    rt.advance(_local(2026, 9, 5, 1, 10))       # 进睡窗 → sleep_preparing
    rt.advance(_local(2026, 9, 5, 1, 45))       # 过 grace → 睡着 + 生成计划
    plan = rt._next_day_plan
    assert plan is not None
    assert plan.local_date == dt.date(2026, 9, 5)          # 醒来那天，不是后天
    by = {i.rule_id: i for i in plan.items}
    assert by["sleep"].planned_start.astimezone(CST).hour == 1
    assert by["work"].planned_start.astimezone(CST).hour == 10   # 整体不偏移，不是 11:40


def _wide_template():
    return {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "baseline",
        "day_profiles": {"baseline": {"allowed_block_ids": ["focus"]}},
        "blocks": [{
            "id": "focus", "category": "role_defined", "activity_pool": ["focus_variant"],
            "preferred_window": {"start": "09:00", "end": "11:00"},
            "duration_minutes": {"min": 30, "max": 90},
            "required": True, "share_policy": "low_pressure",
            "interaction_profile": "occupied_brief", "interaction_impact": "inconvenient",
            "deviation_policy": {"allow_skip": False, "allow_shift": True},
        }],
        "circadian": {
            "wake_window": {"start": "07:00", "end": "09:00"},
            "sleep_window": {"start": "22:00", "end": "00:00"},
            "chronotype": "day_aligned", "recovery_rate_minutes_per_day": 20,
            "target_sleep_minutes": 480,
        },
        "interaction_profiles": {"occupied_brief": {"max_sentences": 2, "question_budget": 0}},
        "autonomy": {"max_deviations_per_day": 1},
    }


def test_offset_still_applies_when_every_block_fits(tmp_path):
    role = tmp_path / "wide"
    role.mkdir()
    (role / "virtual_schedule.json").write_text(
        json.dumps(_wide_template(), ensure_ascii=False), encoding="utf-8")
    rt = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    rt.schedule_offset_minutes = 30
    plan = rt.generate_next_day(dt.datetime(2026, 9, 5, 3, 0, tzinfo=UTC))
    assert plan.items[0].planned_start.astimezone(CST).strftime("%H:%M") == "09:30"


def test_catch_up_after_offline_sleep_builds_today_and_opens_digest():
    """睡窗里进程不在 → 醒来补当日计划 + 放行一次夜眠消化。"""
    rt = _runtime()
    rt.state = ScheduleRuntimeState(state="awake", sleep_cycle_id="xumian:2026-09-05:awake")
    rt.last_sleep_cycle_id = "xumian:2026-09-04"
    rt.advance(_local(2026, 9, 5, 9, 30))
    assert rt._next_day_plan is not None
    assert rt._next_day_plan.local_date == dt.date(2026, 9, 5)
    assert rt.missed_digest_cycle == "xumian:2026-09-04"
    # 已有当日计划（正常一夜）时不再补账
    rt2 = _runtime()
    rt2.advance(_local(2026, 9, 5, 9, 30))
    assert rt2.missed_digest_cycle == ""


def test_tweak_gate_rejects_anchors_and_bad_shapes():
    rt = _runtime()
    when = _local(2026, 9, 5, 1, 30)
    assert rt.queue_schedule_tweaks(
        [{"rule_id": "sleep", "operation": "shift", "shift_minutes": 30}], when) == []
    assert rt.queue_schedule_tweaks(
        [{"rule_id": "work", "operation": "resize", "duration_minutes": 600}], when) == []
    assert rt.queue_schedule_tweaks(
        [{"rule_id": "invented", "operation": "shift", "shift_minutes": 30}], when) == []
    opts = {o["rule_id"] for o in rt.adjustable_blocks(when)}
    assert "sleep" not in opts and "work" in opts


def test_tweak_merges_into_next_plan_and_snapshot_roundtrips():
    rt = _runtime()
    when = _local(2026, 9, 5, 1, 30)
    ok = rt.queue_schedule_tweaks(
        [{"rule_id": "work", "operation": "shift", "shift_minutes": 60,
          "reason": "她说最近项目忙"}], when)
    assert len(ok) == 1
    snap = rt.to_snapshot()
    assert snap["schedule_tweaks"][0]["rule_id"] == "work"
    rt.state = ScheduleRuntimeState(
        state="sleeping", sleep_started_at=when, sleep_cycle_id="xumian:2026-09-04")
    plan = rt.generate_next_day_after_sleep(when)
    work = {i.rule_id: i for i in plan.items}["work"]
    start = work.planned_start.astimezone(CST)
    assert (start.hour, start.minute) == (11, 0)
    assert rt._schedule_tweaks == []
    rt2 = ScheduleRuntime.from_snapshot(ScheduleOutline.from_role_dir(CARD), snap)
    assert rt2.missed_digest_cycle == rt.missed_digest_cycle


# ---------- agent 侧：补账放行一次消化 ----------

class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    base_url = "http://fake"

    def __init__(self, raw=""):
        self.raw = raw
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return self.raw

    def is_model_loaded(self):
        return True


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _agent(tmp_path, llm):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={})


def _seed(a, n=3):
    for txt in ("周一加班到十一点", "周二继续改方案", "周三终于提测了")[:n]:
        mid = a.memory.store_message("user", txt)
        a._store_candidate({"kind": "shared_episode", "content": txt,
                            "source_message_id": mid, "confidence": 0.9,
                            "subject": "user", "source": "rule_extract"})


def test_digest_runs_once_for_missed_cycle(tmp_path):
    llm = FakeLLM(json.dumps({"content": "摘要", "portrait": "", "echo": "",
                              "threads": [], "schedule": []}, ensure_ascii=False))
    a = _agent(tmp_path, llm)
    a.schedule_runtime = _NS(sleeping=False, state=_NS(sleep_cycle_id=""),
                             missed_digest_cycle="xumian:2026-09-04",
                             adjustable_blocks=lambda when: [])
    _seed(a)
    assert a.maybe_nightly_digest()["created"] is True
    assert a.maybe_nightly_digest()["reason"] == "already_digested_cycle"

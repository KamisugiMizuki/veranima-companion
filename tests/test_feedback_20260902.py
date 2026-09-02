"""2026-09-02 真机导出筛查批次的行为级回归（exports/phone-20260902 实锤）。

钉死四件事：
1) 跨角色串台：主动链素材读最近消息必须按角色隔离（许眠引用凛窗口的「真睡了」）
2) 共享单行脏读：非活跃 Agent 的 user_asleep 陈旧内存态不得吞掉作息登记
3) 餐名跟钟点：作息平移后凌晨的餐不得叫「早饭」（00:01 实锤）
4) 羁绊图谱快照迁移幂等（_new 残表）+ 动态归属自愈 + _queue 即时广播钩子
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.proactive import MealReminderScheduler, meal_word
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore

_BRIDGE = Path(__file__).resolve().parents[1] / "android/fuyuno/app/src/main/python/bridge.py"


class Embed:
    dim = 8

    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class LLM:
    def is_model_loaded(self):
        return False

    def chat(self, messages, **kwargs):
        return ""


def _agent(store: MemoryStore, key: str) -> Agent:
    a = Agent(CharacterCard(name=key), store, LLM(), AgentState(), config={"root": "."})
    a.role_key = key  # 生产由 card.source_path 推导；单测直接钉
    return a


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("fuyuno_bridge_0902", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1) 素材读取按角色隔离 ----------

def test_recent_msgs_isolated_by_role(tmp_path):
    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    lin, xm = _agent(store, "lin"), _agent(store, "xumian")
    store.store_message("user", "凛窗口的话", 0.5, "平静", role_id="lin")
    store.store_message("user", "许眠窗口的话", 0.5, "平静", role_id="xumian")
    got = [m["content"] for m in xm._recent_msgs(limit=10)]
    assert got == ["许眠窗口的话"]  # 不再捞到凛窗口的原话
    assert "凛窗口的话" in [m["content"] for m in lin._recent_msgs(limit=10)]


def test_recent_msgs_all_when_no_role_key(tmp_path):
    """PC 单角色时代（role_key=''）=全量，行为不变。"""
    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    a = _agent(store, "")
    store.store_message("user", "A", 0.5, "平静", role_id="lin")
    store.store_message("user", "B", 0.5, "平静", role_id="xumian")
    assert len(a._recent_msgs(limit=10)) == 2


# ---------- 2) 共享单行：脏内存态不得吞登记 ----------

def test_stale_agent_still_registers_wake(tmp_path):
    """许眠 Agent boot 时用户在睡？不——boot 早于凛记录入睡 → 内存 False。
    用户对她报「醒了」时 DB 才是真值（sync 后守卫放行，周期闭合）。"""
    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    lin, xm = _agent(store, "lin"), _agent(store, "xumian")
    now = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)
    assert lin._note_sleep_report("我先睡了", now) == "sleep"
    assert xm.state.user_asleep is False          # xm 是 boot 更早的旧值
    assert xm._note_sleep_report("我醒了", now + dt.timedelta(hours=8)) == "wake"
    assert store.latest_closed_cycle() is not None  # 周期真的闭合了


# ---------- 3) 餐名跟钟点 ----------

def test_meal_word_maps_clock_to_meal():
    assert (meal_word(8), meal_word(12), meal_word(17), meal_word(0), meal_word(23)) == \
        ("早饭", "午饭", "晚饭", "夜宵", "夜宵")


def test_adjust_to_user_cycle_renames_off_slot_meal():
    """真机实锤链：用户 22:26 醒 → 早锚=0 点 → 文案必须改口夜宵。"""
    m = MealReminderScheduler()
    m.adjust_to_user_cycle(22.43)
    hour, text = m.slots["breakfast"]
    assert hour == 0 and "早饭" not in text and "夜宵" in text


# ---------- 4a) 快照迁移幂等（羁绊图谱全空的根因） ----------

def test_snapshot_migration_idempotent_with_leftover_table(bridge):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    # 真机形状：旧表主键=day 单列 + 上次崩一半留下的 _new 残表
    con.execute("CREATE TABLE relationship_snapshots (day TEXT PRIMARY KEY, dims_json TEXT NOT NULL,"
                " updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO relationship_snapshots VALUES ('2026-09-01','{}','x')")
    con.execute("CREATE TABLE relationship_snapshots_new (day TEXT, dims_json TEXT)")
    bridge._ensure_rel_snapshot_table(con)   # 修复前：already exists 直接崩
    bridge._ensure_rel_snapshot_table(con)   # 幂等：二跑不炸不重复建
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='relationship_snapshots'").fetchone()[0]
    assert "PRIMARY KEY (day, role_id)" in ddl
    assert con.execute("SELECT name FROM sqlite_master WHERE name='relationship_snapshots_new'").fetchone() is None


# ---------- 4b) 动态归属自愈 ----------

def test_moment_ownership_backfill(bridge, tmp_path):
    (tmp_path / "characters" / "xumian").mkdir(parents=True)
    (tmp_path / "characters" / "xumian" / "character.json").write_text("{}", encoding="utf-8")
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE moments (id INTEGER PRIMARY KEY, role_id TEXT, content TEXT,"
                " kind TEXT, source_ref TEXT, dedupe_key TEXT UNIQUE, created_at TEXT)")
    con.execute("INSERT INTO moments VALUES (1,'lin','内容','D03','mood','xumian|mood:开心:2026-09-01','t')")
    con.execute("INSERT INTO moments VALUES (2,'yuki','内容2','D01','e','ghost|event:3','t')")
    con.execute("INSERT INTO moments VALUES (3,'xumian','内容3','D01','e','xumian|event:140','t')")
    bridge.boot = SimpleNamespace(root=tmp_path)
    assert bridge._backfill_moment_ownership(con) == 1
    assert con.execute("SELECT role_id FROM moments WHERE id=1").fetchone()[0] == "xumian"
    # 前缀不是已知角色 → 不动；本来就一致 → 不动；重跑幂等
    assert con.execute("SELECT role_id FROM moments WHERE id=2").fetchone()[0] == "yuki"
    assert bridge._backfill_moment_ownership(con) == 0


# ---------- 5) 睡眠窗口对作息报告的回音（P5） ----------

def test_sleeping_window_answers_sleep_report(tmp_path):
    """角色在睡时用户报「醒了」→ 登记不丢且给一句带困意的确认（此前静默吞掉）。"""
    import json as _json

    role = tmp_path / "characters" / "sleeper"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(_json.dumps({
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"},
                      "sleep_window": {"start": "22:00", "end": "23:00"},
                      "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {"grace_period_minutes": 0, "max_extension_minutes": 0},
    }), encoding="utf-8")
    from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    a = _agent(store, "sleeper")
    a.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    start = dt.datetime(2026, 9, 2, 14, tzinfo=dt.timezone.utc)  # 北京 22 点睡窗
    a.schedule_runtime.begin_sleep_preparation(start)
    a.schedule_runtime.extend_wakefulness(start)
    a.memory.save_state(a.state.to_snapshot())

    r_plain = a.handle("别急我再看会文档", now=start)
    assert r_plain.reply == ""                      # 闲聊仍然不打扰睡眠

    a.handle("我先睡了", now=start)                   # 用户周期开账（全局共享事实）
    r_wake = a.handle("我醒了", now=start + dt.timedelta(hours=2))  # 角色仍在睡
    assert "醒" in r_wake.reply                     # 作息报告=浅眠里听见的那一句
    assert a.state.user_asleep is False             # 登记没被吞
    rows = store.sleep_messages("sleeper", "qq:default",
                                a.schedule_runtime.state.sleep_cycle_id)
    assert len(rows) == 3                           # 三条都进了睡眠信箱


# ---------- 4c) _queue 即时广播钩子 ----------

def test_queue_fires_flush_hook(bridge):
    fired = []
    bridge.boot = SimpleNamespace(_flush_hook=lambda: fired.append(1))
    bridge._pending.clear()
    bridge._queue("xumian", "许眠", "text")
    assert fired == [1]
    assert json.loads(bridge.drain_pending())["messages"] == [
        {"role": "xumian", "name": "许眠", "text": "text"}]
    assert fired == [1]  # drain 不重复触发
    bridge._pending.clear()

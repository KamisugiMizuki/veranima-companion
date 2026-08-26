from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

from veranima.app import create_agent
from veranima.config import load_config
from veranima.core.virtual_schedule import ScheduleRuntime

PROJECT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="veranima-schedule-real-") as temp:
        root = Path(temp)
        cfg = copy.deepcopy(load_config())
        cfg["root"] = str(PROJECT)
        cfg["character_card"] = str(PROJECT / "characters" / "zima" / "character.json")
        cfg.setdefault("memory", {})["db_path"] = str(root / "schedule.db")
        cfg.setdefault("search", {})["enabled"] = False
        cfg.setdefault("tasks", {})["enabled"] = False
        cfg["virtual_schedule"] = {"enabled": True, "grace_period_minutes": 0, "max_extension_minutes": 0}
        agent = create_agent(cfg)
        if agent.schedule_runtime is None:
            raise AssertionError("runtime not loaded")
        planner = agent._plan_schedule_with_llm(dt.datetime.now(dt.timezone.utc))
        if planner is not None and not isinstance(planner.get("items"), list):
            raise AssertionError("planner returned invalid structure")
        if not planner or not planner.get("items"):
            raise AssertionError("planner returned no template-bound items")
        runtime = agent.schedule_runtime
        start = dt.datetime.now(dt.timezone.utc)
        runtime.begin_sleep_preparation(start)
        runtime.extend_wakefulness(start)
        before_calls = getattr(agent.llm, "calls", None)
        slept = agent.handle("睡眠期间消息", channel="im")
        if slept.reply != "":
            raise AssertionError("sleeping agent replied")
        rows = agent.memory.sleep_messages(runtime.outline.role_id, "qq:default", runtime.state.sleep_cycle_id)
        if not rows:
            raise AssertionError("sleep message metadata missing")
        wake_at = start + dt.timedelta(minutes=runtime.outline.circadian.target_sleep_minutes + 1)
        runtime.advance(wake_at)
        if runtime.state.state != "awake":
            raise AssertionError("runtime did not wake")
        normal = agent.handle("醒来之后正常回复一句", channel="im")
        if not normal.reply:
            raise AssertionError("awake agent returned empty reply")
        print(json.dumps({
            "ok": True,
            "planner_returned": planner is not None,
            "planner_items": len((planner or {}).get("items") or []),
            "sleep_reply_empty": slept.reply == "",
            "sleep_archive_rows": len(rows),
            "wake_state": runtime.state.state,
            "awake_reply_chars": len(normal.reply),
        }, ensure_ascii=False))
        agent.memory.con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""用真机导出件重放 xumian 的日程时间轴：看 plan.local_date 口径、M3 门、以及 next_plan_* 为何缺席。"""
import datetime as dt, json, sqlite3, sys
from zoneinfo import ZoneInfo
sys.path.insert(0, r"D:\Hermes_workspace\veranima\src")
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime

CARD = r"D:\Hermes_workspace\veranima\exports\phone_20260908\files\characters\xumian"
DB = r"C:\Users\Kamisugi\AppData\Local\Temp\m4_probe.db"

outline = ScheduleOutline.from_role_dir(CARD)
con = sqlite3.connect(DB)
rel = json.loads(con.execute("select relationship from agent_state").fetchone()[0])
snap = rel.get("virtual_schedule_runtime") or {}
print("真机快照键:", sorted(snap.keys()))
print("真机快照 state:", snap.get("state"), "| last_sleep_cycle_id:", snap.get("last_sleep_cycle_id"),
      "| sleep_cycle_id:", snap.get("sleep_cycle_id"))
rt = ScheduleRuntime.from_snapshot(outline, snap, planner=None)
print("重建后 _next_day_plan:", rt._next_day_plan)

zone = ZoneInfo(outline.timezone)
start = dt.datetime(2026, 9, 4, 0, 0, tzinfo=dt.timezone.utc)
end = dt.datetime(2026, 9, 8, 20, 0, tzinfo=dt.timezone.utc)
cur = start
prev = None
while cur <= end:
    rt.advance(cur)
    plan = rt._next_day_plan
    key = (rt.state.state, plan.local_date if plan else None, plan.source if plan else None)
    if key != prev:
        local = cur.astimezone(zone)
        print(f"{local:%m-%d %H:%M} local | state={rt.state.state:<14} reason={rt.state.sleep_reason:<10}"
              f" | plan_date={plan.local_date if plan else None} src={plan.source if plan else None}"
              f" | item={rt.current_item_id!r} ctx={rt.outline.build_day_plan(cur).context_at(cur).activity_category if rt._next_day_plan is None else rt._next_day_plan.context_at(cur).activity_category}")
        prev = key
    cur += dt.timedelta(minutes=15)

print("\n=== 重放结束快照 ===")
out = rt.to_snapshot()
for k in ("state", "sleep_cycle_id", "last_sleep_cycle_id", "next_plan_date", "next_plan_source", "current_item_id"):
    print(f"  {k}: {out.get(k)!r}")

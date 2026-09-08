# -*- coding: utf-8 -*-
"""M4 + 离线补账 的 MuMu #2 实机验收驱动（2026-09-09）。

真机 09-08 导出件三条实锤在此逐条复验（设备钟只向前拨，auto_time 关闭）：
  A 偏移 100min 下生成计划不再炸 advance（旧码 sleep 越窗 → ScheduleTemplateError）；
  B 明日计划 = 醒来那天（旧口径差一天，醒来全落 gap）；
  C 夜间进程不在 → 醒来补当日计划 + 放行一次夜眠消化（旧码双门永久关闭）；
  D digest 的 schedule 微调过闸入池 → 下次生成计划时并入（旧码 M4 未实现）。

用法（veranima venv）：
  python scripts/_mumu_m4_drive.py prep      # 装 APK + 注入 offset=100 + 开钟
  python scripts/_mumu_m4_drive.py night     # 拨进睡窗，断言计划落盘
  python scripts/_mumu_m4_drive.py wake      # 拨醒 + 发 3 条素材
  python scripts/_mumu_m4_drive.py catchup   # 杀掉进程过夜 → 冷启，断言补账
  python scripts/_mumu_m4_drive.py tweak     # 注入微调 → 下一次计划生成并入
  python scripts/_mumu_m4_drive.py inspect   # 只读盘点
  python scripts/_mumu_m4_drive.py restore   # 恢复对时
"""
import datetime
import json
import sys
import time

sys.path.insert(0, r"D:\Hermes_workspace\veranima\scripts")
import mumu_m1_drive as drv  # noqa: E402

LOCAL = datetime.timezone(datetime.timedelta(hours=8))
T_NIGHT_EVE = datetime.datetime(2026, 9, 14, 23, 30)   # 睡前，注入偏移
T_SLEEP_PREP = datetime.datetime(2026, 9, 15, 1, 20)   # 进睡窗
T_SLEEP = datetime.datetime(2026, 9, 15, 2, 0)         # 过 grace → 睡着 + 生成计划
T_WAKE = datetime.datetime(2026, 9, 15, 9, 30)         # 醒来
T_MATERIAL = datetime.datetime(2026, 9, 15, 20, 0)     # 发素材
T_MISSED_MORNING = datetime.datetime(2026, 9, 16, 9, 30)   # 过夜后冷启
T_NEXT_SLEEP = datetime.datetime(2026, 9, 17, 1, 20)   # 下一次生成计划
MATERIAL = [
    "今天项目评审被批了一顿，改到十一点才回家",
    "我妈打电话来问体检结果，我说还没出，其实有点怕",
    "下周三要出差去杭州，三天，回来再补进度",
]


def rel():
    out = drv.sh(["shell", f"su 0 sqlite3 {drv.DB} \"select hex(relationship) from agent_state\""])
    raw = out.strip().splitlines()[-1]
    return json.loads(bytes.fromhex(raw).decode("utf-8"))


def set_rel(data):
    hexs = json.dumps(data, ensure_ascii=False).encode("utf-8").hex()
    drv.sh(["shell", f"su 0 sqlite3 {drv.DB} \"update agent_state set relationship=cast(x'{hexs}' as text)\""])
    back = rel()
    assert back.get("virtual_schedule_runtime"), "回写失败"
    return back


def rt_state():
    return (rel().get("virtual_schedule_runtime") or {})


def decisions(kind=None):
    q = "select kind, action, substr(coalesce(digest,''),1,40) from decisions"
    if kind:
        q += f" where kind like '{kind}%'"
    q += " order by id desc limit 8"
    return drv.sql(q)


def logcat_tail(pat="schedule|digest|traceback|Error"):
    out = drv.sh(["logcat", "-d", "-t", "400"])
    return "\n".join(ln for ln in out.splitlines() if any(p.lower() in ln.lower() for p in pat.split("|")))


def tick(wait=75):
    time.sleep(wait)


def cmd_prep():
    drv.ensure_apk()
    drv.clock_hold(30)
    drv.sh(["shell", "am force-stop " + drv.PKG])
    time.sleep(2)
    data = rel()
    r = data.setdefault("virtual_schedule_runtime", {})
    r["schedule_offset_minutes"] = 100
    r["state"] = "awake"
    r["sleep_started_at"] = None
    r["grace_deadline"] = None
    r["next_plan_date"] = None
    r["missed_digest_cycle"] = None
    r["schedule_tweaks"] = []
    set_rel(data)
    print("[prep] offset=100 已注入；state=awake，计划清空")
    drv.clock_set(T_NIGHT_EVE)
    drv.sh(["shell", f"am start -n {drv.PKG}/.MainActivity"])
    tick(75)
    st = rt_state()
    print("[prep] state=", st.get("state"), "offset=", st.get("schedule_offset_minutes"),
          "plan=", st.get("next_plan_date"))


def cmd_night():
    drv.clock_set(T_SLEEP_PREP)
    tick(80)
    print("[night] 01:20 →", rt_state().get("state"), "plan=", rt_state().get("next_plan_date"))
    drv.clock_set(T_SLEEP)
    tick(90)
    st = rt_state()
    print("[night] 02:00 → state=", st.get("state"), "plan=", st.get("next_plan_date"),
          "src=", st.get("next_plan_source"), "adj=", json.dumps(st.get("next_plan_adjustments") or [], ensure_ascii=False)[:200])
    print("[night] 异常日志：")
    print(logcat_tail("overlap|adjustments unusable|advance failed") or "  （无）")
    assert st.get("next_plan_date") == "2026-09-15", f"计划未落盘/日期错: {st.get('next_plan_date')}"
    print("[night] ✅ 偏移 100min 下计划照常落盘且是醒来那天")


def cmd_wake():
    drv.clock_set(T_WAKE)
    tick(80)
    st = rt_state()
    print("[wake] state=", st.get("state"), "plan=", st.get("next_plan_date"))
    drv.clock_set(T_MATERIAL)
    for text in MATERIAL:
        base = drv.send(text)
        drv.wait_reply(base, timeout=180)
    print("[wake] 素材已发 3 条")


def cmd_catchup():
    drv.sh(["shell", "am force-stop " + drv.PKG])
    time.sleep(2)
    drv.clock_set(T_MISSED_MORNING)
    drv.sh(["shell", f"am start -n {drv.PKG}/.MainActivity"])
    tick(150)
    st = rt_state()
    print("[catchup] state=", st.get("state"), "plan=", st.get("next_plan_date"),
          "missed_cycle=", st.get("missed_digest_cycle"), "tweaks=", st.get("schedule_tweaks"))
    print("[catchup] decisions:")
    print(decisions())
    print("[catchup] 日志：")
    print(logcat_tail("catch-up|digest|not_enough|bad_output") or "  （无）")
    assert st.get("next_plan_date") == "2026-09-16", f"补账未生成当日计划: {st.get('next_plan_date')}"
    assert st.get("missed_digest_cycle") == "xumian:2026-09-15", f"补账 cycle 不对: {st.get('missed_digest_cycle')}"
    print("[catchup] ✅ 补账：当日计划 + 夜眠消化放行")


def cmd_tweak():
    drv.sh(["shell", "am force-stop " + drv.PKG])
    time.sleep(2)
    data = rel()
    r = data["virtual_schedule_runtime"]
    r["schedule_tweaks"] = [{"rule_id": "supper", "operation": "shift", "shift_minutes": 30,
                             "duration_minutes": 20, "activity_key": "supper_variant",
                             "reason": "验收注入"}]
    set_rel(data)
    print("[tweak] 已注入待并入微调（supper +30min）")
    drv.clock_set(T_NEXT_SLEEP)
    drv.sh(["shell", f"am start -n {drv.PKG}/.MainActivity"])
    tick(90)
    drv.clock_set(datetime.datetime(2026, 9, 17, 2, 0))
    tick(90)
    st = rt_state()
    adj = st.get("next_plan_adjustments") or []
    print("[tweak] plan=", st.get("next_plan_date"), "tweaks=", st.get("schedule_tweaks"))
    print("[tweak] adjustments=", json.dumps(adj, ensure_ascii=False)[:300])
    assert any(str(a.get("rule_id")) == "supper" and int(a.get("shift_minutes") or 0) >= 30 for a in adj), \
        f"微调没并入计划: {adj}"
    assert not st.get("schedule_tweaks"), "并入后应清空"
    print("[tweak] ✅ 微调并入下一次计划且池已清空")


def cmd_inspect():
    st = rt_state()
    for k in ("state", "schedule_offset_minutes", "next_plan_date", "next_plan_source",
              "missed_digest_cycle", "schedule_tweaks", "sleep_cycle_id", "last_sleep_cycle_id"):
        print(f"  {k} = {json.dumps(st.get(k), ensure_ascii=False)[:160]}")
    print("  decisions:")
    print(decisions())
    print("  logcat:")
    print(logcat_tail() or "  （无）")


def cmd_restore():
    drv.clock_restore()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    {"prep": cmd_prep, "night": cmd_night, "wake": cmd_wake, "catchup": cmd_catchup,
     "tweak": cmd_tweak, "inspect": cmd_inspect, "restore": cmd_restore}[cmd]()


if __name__ == "__main__":
    main()

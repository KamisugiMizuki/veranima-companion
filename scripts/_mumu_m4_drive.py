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


def _active_entry(data):
    """活跃角色的账在 relationship.roster[卡名] 里（顶层那份是影子，boot 时被
    roster 条目覆盖——09-09 实测：只改顶层 → 冷启后 offset 回 0）。"""
    owner = str(data.get("owner") or "")
    roster = data.get("roster") if isinstance(data.get("roster"), dict) else {}
    if owner and isinstance(roster.get(owner), dict):
        return roster[owner]
    if len(roster) == 1:
        return next(iter(roster.values()))
    return data


def rel():
    out = drv.sh(["shell", f"su 0 sqlite3 {drv.DB} \"select hex(relationship) from agent_state\""])
    raw = out.strip().splitlines()[-1]
    return json.loads(bytes.fromhex(raw).decode("utf-8"))


def set_rel(data):
    """写回 relationship：adb 命令行有长度上限（hex 化 4KB JSON 直接超限，
    09-09 实测被截断→App 读到坏 JSON 回退默认态）。改用设备侧 .read。"""
    sql = "UPDATE agent_state SET relationship='{}';\n".format(
        json.dumps(data, ensure_ascii=False).replace("'", "''"))
    local = r"C:\Users\Kamisugi\AppData\Local\Temp\_mumu_rel_upd.sql"
    with open(local, "w", encoding="utf-8") as fh:
        fh.write(sql)
    drv.sh(["push", local, "/data/local/tmp/_rel_upd.sql"])
    drv.sh(["shell", f"su 0 sqlite3 {drv.DB} '.read /data/local/tmp/_rel_upd.sql'"])
    back = rel()
    assert back.get("virtual_schedule_runtime"), "回写失败"
    return back


def rt_state():
    return (_active_entry(rel()).get("virtual_schedule_runtime") or {})


def decisions(kind=None):
    q = "select ts, kind, verdict, substr(coalesce(digest,''),1,40) from decisions"
    if kind:
        q += f" where kind like '{kind}%'"
    q += " order by id desc limit 8"
    return drv.sql(q)


def logcat_tail(pat="schedule|digest|traceback|Error"):
    out = drv.sh(["logcat", "-d", "-t", "400"])
    return "\n".join(ln for ln in out.splitlines() if any(p.lower() in ln.lower() for p in pat.split("|")))


def tick(wait=75):
    time.sleep(wait)


HOLD = {"target": None}


def _hold_loop():
    """MuMu 的 NTP 会把钟打回真实时间 → 每 45s 重设当前阶段目标（跟随 HOLD）。"""
    while True:
        t = HOLD["target"]
        if t is not None:
            drv.clock_set(t - datetime.timedelta(hours=8))   # 同 clock_set 的本地→UTC 口径
        time.sleep(45)


def clock_set(target):
    """drv.clock_set 把 naive 目标写成 UTC → 设备本地会 +8h；这里先减回本地口径。"""
    HOLD["target"] = target
    drv.clock_set(target - datetime.timedelta(hours=8))


def _patch_rt(mutate):
    """改活跃角色 roster 条目里的 runtime（顶层影子不用管）。"""
    data = rel()
    entry = _active_entry(data)
    r = entry.setdefault("virtual_schedule_runtime", {})
    mutate(r)
    back = set_rel(data)
    return (_active_entry(back).get("virtual_schedule_runtime") or {})


def cmd_prep():
    drv.ensure_apk()
    drv.sh(["shell", "am force-stop " + drv.PKG])
    time.sleep(2)

    def _mut(r):
        r.update({"schedule_offset_minutes": 100, "state": "awake",
                  "sleep_started_at": None, "grace_deadline": None,
                  "next_plan_date": None, "missed_digest_cycle": None,
                  "schedule_tweaks": []})
    st = _patch_rt(_mut)
    assert st.get("schedule_offset_minutes") == 100, f"注入没落库: {st.get('schedule_offset_minutes')}"
    print("[prep] offset=100 已注入 roster[许眠]（回读确认）")
    clock_set(T_NIGHT_EVE)
    drv.sh(["shell", f"am start -n {drv.PKG}/.MainActivity"])
    tick(75)
    st = rt_state()
    print("[prep] 设备钟=", drv.get_clock(), "state=", st.get("state"),
          "offset=", st.get("schedule_offset_minutes"), "plan=", st.get("next_plan_date"))


def cmd_night():
    clock_set(T_SLEEP_PREP)
    tick(80)
    print("[night] 01:20 →", rt_state().get("state"), "plan=", rt_state().get("next_plan_date"))
    clock_set(T_SLEEP)
    tick(90)
    st = rt_state()
    print("[night] 02:00 → state=", st.get("state"), "plan=", st.get("next_plan_date"),
          "src=", st.get("next_plan_source"), "adj=", json.dumps(st.get("next_plan_adjustments") or [], ensure_ascii=False)[:200])
    print("[night] 异常日志：")
    print(logcat_tail("overlap|adjustments unusable|advance failed") or "  （无）")
    assert st.get("next_plan_date") == "2026-09-15", f"计划未落盘/日期错: {st.get('next_plan_date')}"
    print("[night] ✅ 偏移 100min 下计划照常落盘且是醒来那天")


def cmd_wake():
    clock_set(T_WAKE)
    tick(80)
    st = rt_state()
    print("[wake] state=", st.get("state"), "plan=", st.get("next_plan_date"))
    clock_set(T_MATERIAL)
    for text in MATERIAL:
        base = drv.send(text)
        drv.wait_reply(base, timeout=180)
    print("[wake] 素材已发 3 条")


def cmd_catchup():
    drv.sh(["shell", "am force-stop " + drv.PKG])
    time.sleep(2)
    clock_set(T_MISSED_MORNING)
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

    def _mut(r):
        r["schedule_tweaks"] = [{"rule_id": "supper", "operation": "shift", "shift_minutes": 30,
                                 "duration_minutes": 20, "activity_key": "supper_variant",
                                 "reason": "验收注入"}]
    st = _patch_rt(_mut)
    assert st.get("schedule_tweaks"), "微调注入没落库"
    print("[tweak] 已注入待并入微调（supper +30min）")
    clock_set(T_NEXT_SLEEP)
    drv.sh(["shell", f"am start -n {drv.PKG}/.MainActivity"])
    tick(90)
    clock_set(datetime.datetime(2026, 9, 17, 2, 0))
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


def cmd_more():
    """补素材：digest 要 ≥3 条非张力账本的 episodic；judges 只对 event/commitment
    类判词落 episodic，所以补几条明确事件。"""
    extra = ["周六要去医院拿体检报告", "下周要把工位搬到十二楼",
             "昨天室友搬走了，家里一下安静了", "这周五要交季度总结"]
    for text in extra:
        base = drv.send(text)
        drv.wait_reply(base, timeout=180)
    q = ("select count(*) from memories where layer='episodic' "
         "and created_at >= '2026-09-15T01:30:00' and coalesce(meta,'') not like '%relational_tension%'")
    print("[more] 窗口内非账本 episodic =", drv.sql(q))


def cmd_produce():
    """补测 M4 生产者：真素材喂够 → 下一夜 digest 第五格 → 过闸入池。"""
    drv.ensure_apk()
    clock_set(datetime.datetime(2026, 9, 17, 9, 30))
    tick(80)
    print("[produce] 醒后 state=", rt_state().get("state"))
    for text in ["明晚我八点就到家了，想跟你多聊会儿",
                 "下周三来杭州，晚上八点以后都有空",
                 "这周五交完季度总结就能歇两天",
                 "最近睡太晚了，明天开始想十一点就躺下"]:
        base = drv.send(text)
        drv.wait_reply(base, timeout=180)
    q = ("select count(*) from memories where layer='episodic' "
         "and created_at >= '2026-09-17T00:00:00' and coalesce(meta,'') not like '%relational_tension%'")
    print("[produce] 09-17 窗口内非账本 episodic =", drv.sql(q))
    clock_set(datetime.datetime(2026, 9, 18, 1, 20))
    tick(80)
    clock_set(datetime.datetime(2026, 9, 18, 2, 0))
    tick(100)
    tick(150)
    r = rt_state()
    print("[produce] state=", r.get("state"), "plan=", r.get("next_plan_date"),
          "tweaks=", json.dumps(r.get("schedule_tweaks"), ensure_ascii=False))
    print("[produce] reflect:* 最新：")
    print(drv.sql("select ts, kind, verdict, substr(coalesce(digest,''),1,60) from decisions "
                  "where kind like 'reflect%' order by id desc limit 5"))
    out = drv.sh(["logcat", "-d", "-t", "900"])
    for ln in out.splitlines():
        if "schedule slot" in ln or "schedule accepted" in ln or "nightly digest" in ln:
            print("[log]", ln[-220:])


def cmd_produce2():
    """M4 生产者二轮：先喂到窗口内 ≥3 条真实 episodic（首轮 4 条消息只留 1 条，
    其余被判词行/粒度闸吃掉），再看下一夜 digest 的 schedule 格。"""
    drv.ensure_apk()
    clock_set(datetime.datetime(2026, 9, 18, 10, 0))
    tick(80)
    print("[p2] 醒后 state=", rt_state().get("state"), "clock=", drv.get_clock())
    more = [
        "今天下班路上碰到大学室友，他刚搬到杭州，约我周末去他家吃饭",
        "我妈今天打电话问过年回不回家，我说还没定，她在那头沉默了几秒",
        "楼下那只猫今天生了四只小猫，我蹲那儿看了半小时，被蚊子咬了一腿",
        "公司楼下新开了家面馆，老板是陕西人，辣椒油香得我每天中午都想去",
    ]
    for m in more:
        base = drv.send(m)
        drv.wait_reply(base, timeout=150)
    n = drv.sql("select count(*) from memories where layer='episodic' "
                "and created_at >= '2026-09-18T00:00:00' and content not like '%判断点%'")
    print("[p2] 窗口内真实 episodic =", n)
    clock_set(datetime.datetime(2026, 9, 19, 1, 20))
    tick(80)
    print("[p2] 01:20 →", rt_state().get("state"))
    clock_set(datetime.datetime(2026, 9, 19, 2, 0))
    tick(130)
    st = rt_state()
    print("[p2] 02:00 → state=", st.get("state"), "plan=", st.get("next_plan_date"),
          "src=", st.get("next_plan_source"))
    tick(150)
    st = rt_state()
    print("[p2] tweaks=", json.dumps(st.get("schedule_tweaks") or [], ensure_ascii=False)[:300])
    print("[p2] adj=", json.dumps(st.get("next_plan_adjustments") or [], ensure_ascii=False)[:300])
    log = drv.sh(["shell", f"su 0 tail -n 500 /data/data/{drv.PKG}/files/logs/core.log"])
    hits = [ln for ln in log.splitlines()
            if any(k in ln for k in ("digest", "reflect:schedule", "schedule slot", "nightly"))]
    print("[p2] digest 日志：")
    print("\n".join(hits[-16:]) or "  （无）")
    print("[p2] reflect 账：")
    print(decisions("reflect:"))


def cmd_consume():
    """M4 消费者收口：digest 入池的微调 → 下一次生成计划并入 → 池清空。"""
    st = rt_state()
    print("[consume] 入池前 tweaks=", json.dumps(st.get("schedule_tweaks") or [], ensure_ascii=False))
    clock_set(datetime.datetime(2026, 9, 20, 1, 20))
    tick(80)
    clock_set(datetime.datetime(2026, 9, 20, 2, 0))
    tick(120)
    st = rt_state()
    print("[consume] plan=", st.get("next_plan_date"),
          "adj=", json.dumps(st.get("next_plan_adjustments") or [], ensure_ascii=False))
    print("[consume] 池=", json.dumps(st.get("schedule_tweaks") or [], ensure_ascii=False))


def cmd_restore():
    drv.clock_restore()


def main():
    import threading
    threading.Thread(target=_hold_loop, daemon=True).start()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    {"prep": cmd_prep, "night": cmd_night, "wake": cmd_wake, "catchup": cmd_catchup,
     "tweak": cmd_tweak, "more": cmd_more, "produce": cmd_produce, "produce2": cmd_produce2,
     "consume": cmd_consume,
     "inspect": cmd_inspect, "restore": cmd_restore}[cmd]()


if __name__ == "__main__":
    main()

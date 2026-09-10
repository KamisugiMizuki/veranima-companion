# -*- coding: utf-8 -*-
"""出现=醒来（活动集群版）MuMu #2 实机验收驱动（2026-09-11）。

真链路：UsageStats 前台事件 → CompanionService 轮询 → bridge.wake_signal →
Agent.note_presence_signal（集群/最近信号/松开 asleep）→ agent_state；
报告链走 drive_b64 注入口（真 judges → _note_sleep_report → 闭合定案）。

验收项：
  A  入睡报告：user_asleep=1 + 周期开
  B  <4h 防线：入睡 4h 内的活动信号不记录（也不松开）
  C  ≥4h 信号：记录集群起点+最近信号，松开 asleep（她知道但不说）
  D  同集群滚动：短间隔二次信号不重开集群、只滚 last
  E  集群重建：>90min 间隔的信号开新集群
  F  报告定案：报告与活动流相连 → cycle.woke_at = 集群起点（≠报告时刻）

用法（veranima venv）：
  python scripts/_mumu_wake_drive.py run
  python scripts/_mumu_wake_drive.py inspect
  python scripts/_mumu_wake_drive.py restore
"""
import datetime
import json
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"D:\Hermes_workspace\veranima\scripts")
import mumu_m1_drive as drv  # noqa: E402

OUT = r"D:\Hermes_workspace\veranima\exports\mumu_wake_drive.json"
RESULT = {"steps": [], "verdicts": {}}


def log(msg):
    print(msg, flush=True)


def recall(key, ok, detail=""):
    RESULT["verdicts"][key] = {"ok": bool(ok), "detail": str(detail)}
    log(f"[{'PASS' if ok else 'FAIL'}] {key} {detail}")


def parse_local(s):
    dt = datetime.datetime.fromisoformat(str(s))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def state3():
    out = drv.sql(
        "select ifnull(user_asleep,0)||'|'||ifnull(inferred_woke_at,'')||'|'||"
        "ifnull(last_signal_at,'') from agent_state where id=1")
    parts = (out or "").strip().split("|")
    if len(parts) != 3:
        return None
    return {"asleep": parts[0] == "1", "inferred": parts[1], "last": parts[2]}


def latest_cycle():
    out = drv.sql(
        "select id||'|'||fell_asleep_at||'|'||ifnull(woke_at,'')||'|'||"
        "ifnull(substr(summary,1,60),'') from sleep_cycles order by id desc limit 1")
    parts = (out or "").strip().split("|", 3)
    if len(parts) != 4:
        return None
    return {"id": parts[0], "fell": parts[1], "woke": parts[2], "summary": parts[3]}


def wait_for(fn, ok, timeout=90, interval=5, desc=""):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = fn()
        if last is not None and ok(last):
            return last
        time.sleep(interval)
    log(f"[wait] {desc} 超时 {timeout}s, last={last}")
    return None


# 设备时钟的 -u 语义与本地面差（09-11 实测：date -u 设为 x → 本地读数 = x + 本地面偏移），
# 探测后所有「本地时钟面」目标走 set_local 换算（MuMu #2 实测 OFFSET=+8h）
OFFSET = datetime.timedelta(0)


def set_local(dt):
    drv.clock_set(dt - OFFSET)


def clock_probe():
    global OFFSET
    t0 = drv.get_clock()
    probe = t0 + datetime.timedelta(minutes=30)
    drv.clock_set(probe)
    time.sleep(2)
    t1 = drv.get_clock()
    OFFSET = t1 - probe
    set_local(t0)
    time.sleep(1)
    log(f"[clock] 探测: param={probe} 读回={t1} OFFSET={OFFSET}")
    return OFFSET


class HoldClock:
    """单线程按住设备钟：目标可变（m1 的 clock_hold 是单值快照，拨一次就失效）。"""

    def __init__(self):
        self.target = None
        self.stop = False
        self.th = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.th.start()

    def set(self, dt):
        self.target = dt
        set_local(dt)
        log(f"[clock] +hold → {dt:%m-%d %H:%M:%S}")

    def halt(self):
        self.stop = True
        self.th.join(timeout=15)

    def _run(self):
        while not self.stop:
            t = self.target
            if t is not None and not self.stop:
                try:
                    set_local(t)
                except Exception:
                    pass
            for _ in range(20):
                if self.stop:
                    break
                time.sleep(0.5)


def to_settings():
    drv.sh(["shell", "am start -a android.settings.SETTINGS"])


def to_home():
    drv.sh(["shell", "input keyevent 3"])


def cmd_run():
    # ---- S0 安装/权限/清场/时钟探测 ----
    log("== S0 安装/权限/清场 ==")
    log("[apk] " + drv.sh(["install", "-r", drv.APK]).strip()[-140:])
    drv.sh(["shell", f"cmd appops set {drv.PKG} GET_USAGE_STATS allow"])
    log("[appops] " + drv.sh(["shell", f"cmd appops get {drv.PKG} GET_USAGE_STATS"]).strip()[:120])
    # 首启一次跑 init_db 迁移（新列就位后再清场——09-11 首跑实锤：迁移前 UPDATE 新列
    # 整条报错 → asleep 残留 → 「睡了」报告被 not-asleep 吞掉，周期不开）
    drv.sh(["shell", f"am start -n {drv.PKG}/.MainActivity"])
    log("[app] 首启跑迁移，等 25s")
    time.sleep(25)
    drv.sh(["shell", "am force-stop " + drv.PKG])
    drv.sql("delete from sleep_cycles")
    drv.sql("update agent_state set user_asleep=0, last_sleep_report_at='' where id=1")
    drv.sql("update agent_state set inferred_woke_at='', last_signal_at='' where id=1")
    log(f"[db] 清场完成 state={state3()}")

    probe = clock_probe()
    recall("clock_probe", abs(probe.total_seconds()) < 12 * 3600, f"OFFSET={probe}")

    hold = HoldClock()
    hold.start()

    # ---- S1 入睡报告（send 冷启 app，全链）----
    log("== S1 入睡报告 ==")
    drv.send("准备去睡了，晚安")
    st = wait_for(state3, lambda s: s and s["asleep"], timeout=150, desc="user_asleep=1")
    recall("A_sleep_report", st is not None and st["asleep"],
           json.dumps(st, ensure_ascii=False))
    cyc = latest_cycle()
    log(f"[db] cycle={cyc}")
    fell = cyc["fell"] if cyc else ""
    recall("A_cycle_open", bool(cyc) and not cyc["woke"] and bool(fell), f"fell={fell}")

    # ---- S2 <4h 防线（负向）----
    log("== S2 <4h 防线（负向） ==")
    to_settings()
    time.sleep(45)
    st = state3()
    recall("B_blocked_under_4h", bool(st) and not st["inferred"] and st["asleep"],
           json.dumps(st, ensure_ascii=False))

    # ---- S3 ≥4h 信号（设置→桌面 切换产生 RESUMED）----
    log("== S3 ≥4h 信号 ==")
    hold.set(parse_local(fell) + datetime.timedelta(hours=5, minutes=15))
    to_home()
    time.sleep(45)
    st = wait_for(state3, lambda s: s and s["inferred"], timeout=90, desc="inferred 写入")
    recall("C_signal_ge4h",
           bool(st) and not st["asleep"] and st["inferred"] and st["inferred"] == st["last"],
           json.dumps(st, ensure_ascii=False))
    sig1 = st["inferred"] if st else ""

    # ---- S4 同集群滚动（桌面→设置 再切换一次）----
    log("== S4 同集群滚动 ==")
    to_settings()
    time.sleep(45)
    st = state3()
    recall("D_cluster_roll", bool(st) and st["inferred"] == sig1 and st["last"] >= sig1,
           f"sig1={sig1} state={st}")
    sig2 = st["last"] if st else sig1

    # ---- S5 集群重建（拨 +3h10m 再切）----
    log("== S5 集群重建（>GAP） ==")
    hold.set(drv.get_clock() + datetime.timedelta(hours=3, minutes=10))
    to_home()
    time.sleep(45)
    st = wait_for(state3, lambda s: s and s["inferred"] != sig1, timeout=90, desc="新集群")
    recall("E_cluster_rebuild", bool(st) and st["inferred"] != sig1,
           f"sig1={sig1} → {st}")
    sig3 = st["inferred"] if st else ""

    # ---- S6 报告定案 ----
    log("== S6 报告定案 ==")
    hold.set(drv.get_clock() + datetime.timedelta(minutes=3))
    drv.send("醒了，起了")
    cyc = wait_for(latest_cycle, lambda c: c and c["woke"], timeout=180, desc="cycle 闭合")
    recall("F_wake_docked_to_cluster", bool(cyc) and cyc["woke"] == sig3,
           f"woke={cyc['woke'] if cyc else '?'} cluster={sig3}")
    rep = drv.sql("select created_at from messages where role='user' order by id desc limit 1")
    log(f"[db] 报告消息时刻={rep} / cycle={cyc}")
    RESULT["report_msg_at"] = rep
    RESULT["cycle"] = cyc
    RESULT["signals"] = {"sig1": sig1, "sig2": sig2, "sig3": sig3}

    # ---- S7 收尾 ----
    log("== S7 恢复对时 ==")
    hold.halt()
    drv.clock_restore()
    ev = drv.sh(["shell", "su 0 grep -E 'presence signal|user sleep reported|user wake reported' "
                 f"/data/data/{drv.PKG}/files/logs/core.log | tail -12"])
    log("[core.log 证据]\n" + ev)
    RESULT["core_log"] = ev
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(RESULT, f, ensure_ascii=False, indent=1)
    log(f"[out] → {OUT}")
    ok = all(v["ok"] for v in RESULT["verdicts"].values())
    log(f"===== 总判定: {'全过' if ok else '有 FAIL'} =====")
    return ok


def cmd_inspect():
    log(f"clock={drv.get_clock()}")
    log(f"state={state3()}")
    log(f"cycle={latest_cycle()}")
    log("pkg=" + drv.sh(["shell", f"dumpsys package {drv.PKG} | grep lastUpdateTime"]).strip()[:80])


def cmd_restore():
    drv.clock_restore()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "inspect", "restore"])
    a = ap.parse_args()
    if a.cmd == "run":
        cmd_run()
    elif a.cmd == "inspect":
        cmd_inspect()
    else:
        cmd_restore()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""M1 真机验收数据脚本化（MuMu #2，127.0.0.1:16448）。

用户裁决 2026-09-07：造数据直接在模拟器上做。原理=发送链盖的戳全是设备本地
naive 时间（bridge _now → Python datetime.now()），所以「隔 N 天」不需要真等：
发消息 → adb root 改 system clock（只向前拨）→ 下条消息/主动 tick 全用新钟。
judges/thread 账/睡眠认领闸/作息 adapt 走的全部是真实链路，只压缩等待。

用法（veranima venv）：
  python scripts/mumu_m1_drive.py phase1     # 两件事+睡眠报告（拨钟 2 天）
  python scripts/mumu_m1_drive.py inspect    # 导库跑判定（不发消息不改钟）
  python scripts/mumu_m1_drive.py phase2     # 完结+编造红线（拨钟 2 天）
  python scripts/mumu_m1_drive.py inspect --keep   # 收尾恢复对时
"""
import argparse
import datetime
import json
import subprocess
import sys
import time

ADB = r"D:\Android-sdk\platform-tools\adb.exe"
SERIAL = "127.0.0.1:16448"
PKG = "io.github.kamisugimizuki.veranima"
APK = (r"D:\Hermes_workspace\veranima\android\fuyuno\app"
       r"\build\outputs\apk\debug\app-debug.apk")
DB = f"/data/data/{PKG}/files/data/veranima.db"

# 话术（对许眠说，带情绪让 judges 判得出 thread_candidate）
T_FRI = "周五要交毕设初稿，还差一大截没写完，烦"
T_CHECKUP = "下周要去体检复查，上次指标不太好，有点慌"
T_NIGHTMARE = "昨晚又没睡好，三点多才睡着"
T_CLOSE = "跟你说一声，初稿昨天交了，老师回了句还行"
T_ROOMMATE = "我室友最近天天半夜打电话，吵死了"   # 从未给过背景 → 编造红线


def sh(cmd, **kw):
    r = subprocess.run([ADB, "-s", SERIAL] + cmd, capture_output=True, text=True, **kw)
    return (r.stdout or "") + (r.stderr or "")


def ensure_apk(force=False):
    import os
    src = r"D:\Hermes_workspace\veranima\src\veranima"
    newest = max((os.path.getmtime(os.path.join(dp, f))
                  for dp, _, fs in os.walk(src) for f in fs if f.endswith(".py")), default=0)
    installed = 0
    out = sh(["shell", f"dumpsys package {PKG} | grep lastUpdateTime"])
    try:
        ts = out.split("=", 1)[1].strip().split("    ")[0].strip()
        installed = datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        installed = 0
    if force or newest > installed + 60:
        print(f"[apk] 源码 mtime={datetime.datetime.fromtimestamp(newest):%m-%d %H:%M} > "
              f"装机 {ts if installed else '?'} → 重打包")
        subprocess.run([r"D:\Android-sdk\gradle-8.13\bin\gradle.bat", ":app:assembleDebug",
                        "--no-daemon", "-q"], cwd=r"D:\Hermes_workspace\veranima\android\fuyuno",
                       env={**__import__("os").environ,
                            "JAVA_HOME": r"D:\Android-sdk\jdk-17.0.20.1+1"}, check=True)
        print("[apk]", sh(["install", "-r", APK]).strip())
    else:
        print("[apk] 装机版本已含全部源码改动")


def send(text):
    """am start --esa drive_b64 → MainActivity 测试注入口 → bridge.chat 全链
    （judges/线程/落库/回复；仅 debug APK，真机裁决=测试内容绝不装）。
    返回基线行 id（=发送时刻 max(id)，wait_reply 等 id>基线的 assistant 行）。"""
    b64 = __import__("base64").b64encode(text.encode()).decode()
    base = sql("select max(id) from messages") or "0"
    sh(["shell", "am force-stop " + PKG])  # 冷启保证 onCreate 收到带 extra 的 intent
    out = sh(["shell",
              f"am start -n {PKG}/.MainActivity --esa drive_b64 '{b64}'"])
    ok = "Starting" in out and "Error" not in out
    print(f"[send] {text[:24]}… {'OK' if ok else out.strip()[:120]}")
    return int(base)


def wait_reply(msg_id, timeout=240):
    """等 bridge.chat 落库：出现 id>msg_id 的 assistant 行。boot 要 ~10s，
    所以第一次检查前先等 15s 给进程起+判+回全链。（09-07 实锤：不看基线=
    上一条旧回复骗过检查，8 秒就 force-stop 腰斩本条。）"""
    time.sleep(15)
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = sh(["shell",
                  f"su 0 sqlite3 {DB} \"select id from messages where id>{msg_id} "
                  f"and role='assistant' limit 1\""]).strip()
        if row.isdigit():
            txt = sh(["shell", f"su 0 sqlite3 {DB} \"select substr(content,1,28) from messages "
                               f"where id={row}\""]).strip()
            print(f"[reply] #{row} {txt}")
            return int(row)
        time.sleep(6)
    print("[reply] 超时未见新 assistant 行")
    return None


def clock_set(dt: datetime.datetime):
    sh(["shell", "settings put global auto_time 0; settings put system auto_time 0"])
    sh(["shell", f"su 0 date -u {dt.strftime('%m%d%H%M%Y.%S')}"])
    print(f"[clock] → {dt:%Y-%m-%d %H:%M:%S} (auto_time off ×global/system)")


def clock_hold(minutes=3):
    """后台按住钟：MuMu 网络恢复/NTP 触发会把钟打回真实时间（09-07 实锤：
    date 两分钟翻回 09-07，配对时间戳倒挂）。起一个循环线程每分钟强制重设。"""
    import threading
    cur = get_clock()

    def _hold():
        end = time.time() + minutes * 60
        while time.time() < end:
            clock_set(cur)
            time.sleep(45)
    threading.Thread(target=_hold, daemon=True).start()


def sql(q):
    return sh(["shell", f"su 0 sqlite3 {DB} \"{q.replace(chr(34), '')}\""]).strip()


def clock_restore():
    sh(["shell", "settings put system auto_time 1"])
    sh(["shell", "am broadcast -a android.intent.action.TIME_SET"])
    print("[clock] auto_time=1 已恢复（对时到真实时间）")


def get_clock():
    out = sh(["shell", "date '+%Y-%m-%d %H:%M:%S'"])
    return datetime.datetime.strptime(out.strip(), "%Y-%m-%d %H:%M:%S")


def dumpdb(dst):
    """设备侧 sqlite3 .dump 文本中转（二进制 db+wal 拷贝会撕裂快照——09-07 实测
    malformed；先 checkpoint 再 dump 双保险）。"""
    sh(["shell", f"su 0 sqlite3 {DB} 'PRAGMA wal_checkpoint(TRUNCATE);'"])
    dump = sh(["exec-out", f"su 0 sqlite3 {DB} .dump"])
    if "BEGIN TRANSACTION" not in dump:
        print(f"[db] dump 失败: {dump[:120]}")
        return None
    import sqlite3
    for cand in (dst, dst.replace(".db", "_v2.db"), dst.replace(".db", "_v3.db")):
        try:
            import os
            if os.path.exists(cand):
                os.remove(cand)
            con = sqlite3.connect(cand)
            con.executescript(dump)
            con.commit(); con.close()
            print(f"[db] -> {cand}")
            return cand
        except OSError:
            continue  # 旧文件被上个进程占用（09-07：verdict 里漏 close 的连接）
    print("[db] 全部候选名被占用")
    return None


def triage(db):
    subprocess.run([sys.executable, "scripts/triage_check.py", db],
                   cwd=r"D:\Hermes_workspace\veranima")


def verdict(db):
    """M1 验收判定：全部对着导出的库算，不碰设备。"""
    import sqlite3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    def rows(q, *a):
        try:
            return [dict(r) for r in con.execute(q, a)]
        except Exception:
            return []
    th = rows("SELECT id,topic,intensity,status,origin,updated_at FROM mind_threads")
    open_th = [t for t in th if t.get("status") == "open"]
    # 睡眠认领：每周期 summary 只出现一次（claimed 列 + wakesummary 账）
    cyc = rows("SELECT id,summary,claimed FROM sleep_cycles")
    sums = [c for c in cyc if (c.get("summary") or "").strip()]
    wk = rows("SELECT object_ref, verdict FROM decisions WHERE kind='wakesummary'")
    # 编造：室友出现过的助手消息（红线=她不许自己补细节，人读用）
    lie = rows("SELECT content FROM messages WHERE role='assistant' AND content LIKE '%室友%'")
    rel = (rows("SELECT relationship FROM agent_state WHERE id=1") or [{}])[0].get("relationship") or "{}"
    import json as _json
    veto = _json.loads(rel).get("proactive_veto") if isinstance(rel, str) else {}
    print(_json.dumps({
        "threads": [{"id": t["id"], "topic": t["topic"][:18], "intensity": t["intensity"],
                     "status": t["status"]} for t in th],
        "open": len(open_th),
        "thread_turns_weaved": [r["object_ref"] for r in wk],
        "sleep_cycles": len(cyc), "summaries": len(sums),
        "wakesummary_sent": sum(1 for r in wk if r["verdict"] == "sent"),
        "proactive_veto": veto,
        "roommate_msgs(人读红线审计)": [m["content"][:60] for m in lie],
    }, ensure_ascii=False, indent=1))
    con.close()


def phase(n, msgs, jump_days, gap_s=90):
    clock = get_clock()
    target = clock + datetime.timedelta(days=jump_days)
    print(f"=== phase{n} 设备钟 {clock:%m-%d %H:%M} → {target:%m-%d %H:%M} ===")
    for text in msgs:
        base = send(text)
        if wait_reply(base) is None:
            print(f"[phase{n}] {text[:16]}… 失败，中止")
            return False
        time.sleep(gap_s)  # 拉开间隔，让主动 tick 不至于全挤同一窗口
    clock_set(target)
    clock_hold(2)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["phase1", "phase2", "inspect", "finish"])
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--force-apk", action="store_true")
    a = ap.parse_args()
    if a.cmd == "phase1":
        ensure_apk(a.force_apk)
        # day1：两件心事带情绪 + 一条睡眠报告（新苏醒周期=认领闸真身）
        phase(1, [T_FRI, T_CHECKUP, T_NIGHTMARE], 2)
        # day3：故意不提她 → 看主动线（spoke 0.45）今晚/明早自己问不问
    elif a.cmd == "phase2":
        clock = get_clock()
        if clock < datetime.datetime(2026, 9, 9):
            print("phase1 还没拨钟/没跑"); return
        # 主动线已过一轮（tick 靠 60s 心跳自动织发；跑前先看 inspect）
        print("[hint] 跑 phase2 前先 inspect 看一眼 day3 的主动消息有没有进池。")
        phase(2, [T_CLOSE, T_ROOMMATE], 2)
    elif a.cmd == "inspect":
        db = dumpdb(r"D:\Hermes_workspace\veranima\exports\mumu_m1.db")
        if db:
            verdict(db); triage(db)
        if not a.keep:
            clock_restore()
    elif a.cmd == "finish":
        db = dumpdb(r"D:\Hermes_workspace\veranima\exports\mumu_m1.db")
        if db:
            verdict(db)
        clock_restore()


if __name__ == "__main__":
    main()

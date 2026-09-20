"""R09（USER_MOOD）MuMu 跨天观察驱动器（2026-09-20）。

真机 7 天验收（§5-4）的压缩版：用拨钟把「跨天」压进一次会话，走同一条真实生产链
（am start --esa drive_b64 → bridge.chat → judges → 落库），不 mock。

用法：
    python scripts/_mumu_r09_drive.py <stage>
      prep      看基线/窗口现状（不改任何东西）
      short     连发 4 条短句（第 4 条应触发对照）
      check     读 decisions(user_mood) + agent_state.user_mood_json + 最近回复
      nextday   拨钟 +1 天（关 auto_time，冻结）
      correct   发一条正常长度的消息（同日纠正路径）
      restore   恢复 auto_time 并核对

只动 MuMu 测试实例（127.0.0.1:16448）；真机永不推测试内容。
"""

from __future__ import annotations

import base64
import subprocess
import sys
import time

ADB = r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe"
SERIAL = "127.0.0.1:16448"
PKG = "io.github.kamisugimizuki.veranima"
DB = f"/data/data/{PKG}/files/data/veranima.db"


def sh(*args: str, timeout: int = 120) -> str:
    r = subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (r.stdout or "").strip() or (r.stderr or "").strip()


def sql(query: str) -> str:
    return sh("shell", f'su 0 sqlite3 {DB} "{query}"')


def send(text: str, wait: float = 22.0) -> None:
    """投一条消息走生产链（bridge.chat）。

    am start --esa 收两个参数（key 空格 value）；写成 key=value 会静默不投递。
    而且 app 已在栈顶时 intent 只会投给现有实例（onCreate 不再跑、extra 被丢），
    所以每条先 force-stop 再起——代价是每条重 boot 一次（~10s），换掉改产码做测试口。
    """
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    sh("shell", "am", "force-stop", PKG)
    time.sleep(1.0)
    out = sh("shell", "am", "start", "-n", f"{PKG}/.MainActivity", "--esa", "drive_b64", b64)
    if "Error" in out:
        print(f"  ! am start: {out[:120]}")
    time.sleep(wait)


def stage_prep() -> None:
    print("== 用户消息长度（最近 20 条，各角色）==")
    for role in ("xumian", "lin"):
        row = sql(f"SELECT COUNT(*), CAST(AVG(len) AS INT), MAX(len) FROM (SELECT LENGTH(TRIM(content)) AS len "
                  f"FROM messages WHERE role='user' AND role_id='{role}' ORDER BY id DESC LIMIT 20);")
        print(f"  {role}: {row}")
    print("== 最近 6 条用户消息 ==")
    print(sql("SELECT id, role_id, LENGTH(TRIM(content)), substr(content,1,16) FROM messages "
              "WHERE role='user' ORDER BY id DESC LIMIT 6;"))
    print("== 当日态 ==")
    print(sql("SELECT user_mood_json FROM agent_state WHERE id=1;"))


def stage_short() -> None:
    for text in ("嗯", "好", "算了", "随便"):
        send(text)
        print(f"sent: {text}")
    print("== 落库确认 ==")
    print(sql("SELECT id, LENGTH(TRIM(content)), substr(content,1,10) FROM messages "
              "WHERE role='user' ORDER BY id DESC LIMIT 5;"))


def stage_check() -> None:
    print("== decisions: judge:user_mood ==")
    print(sql("SELECT id, ts, role_id, kind, verdict, reason FROM decisions "
              "WHERE kind LIKE 'judge:user_mood%' ORDER BY id DESC LIMIT 5;") or "(无)")
    print("== 当日态 ==")
    print(sql("SELECT user_mood_json FROM agent_state WHERE id=1;") or "(空)")
    print("== 最近一轮回复（看她软没软）==")
    print(sql("SELECT id, role, LENGTH(TRIM(content)), substr(REPLACE(content, char(10), ' '),1,60) "
              "FROM messages ORDER BY id DESC LIMIT 4;"))
    print("== 全部 decisions 计数（确认留痕没爆表）==")
    print(sql("SELECT kind, COUNT(*) FROM decisions GROUP BY kind ORDER BY 2 DESC LIMIT 6;"))


def stage_nextday() -> None:
    """拨钟 +1 天：auto_time 的真闸在 global 命名空间（09-07 实锤）。"""
    print(sh("shell", "su 0 settings put global auto_time 0"))
    y, m, d, hh, mm, ss = _device_time()
    print(f"设备当前: {y}-{m}-{d} {hh}:{mm}:{ss}")
    # 拨到「明天同一时刻 + 2 小时」，UTC 格式 MMDDhhmmYYYY.ss
    import datetime as dt
    nxt = dt.datetime(y, m, d, hh, mm, ss) + dt.timedelta(days=1, hours=2)
    stamp = nxt.strftime("%m%d%H%M%Y.%S")
    print(sh("shell", f"su 0 date -u {stamp}"))
    print("设备时间现在:", sh("shell", "date"))
    print("auto_time:", sh("shell", "settings get global auto_time"))


def stage_correct() -> None:
    send("今天就是赶工，下午把方案改完，晚上还得跑一遍回归，倒没什么事")
    time.sleep(3)
    print("== 当日态（同日纠正应被解除）==")
    print(sql("SELECT user_mood_json FROM agent_state WHERE id=1;") or "(空=已解除)")


def stage_restore() -> None:
    sh("shell", "su 0 settings put global auto_time 1")
    sh("shell", "am", "broadcast", "-a", "android.intent.action.TIME_SET")
    time.sleep(5)
    print("auto_time:", sh("shell", "settings get global auto_time"))
    print("设备时间:", sh("shell", "date"))


def _device_time():
    raw = sh("shell", "date '+%Y %m %d %H %M %S'")
    return tuple(int(x) for x in raw.split()[:6])


STAGES = {
    "prep": stage_prep,
    "short": stage_short,
    "check": stage_check,
    "nextday": stage_nextday,
    "correct": stage_correct,
    "restore": stage_restore,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in STAGES:
        print(__doc__)
        raise SystemExit(2)
    STAGES[sys.argv[1]]()

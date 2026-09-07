"""HARNESS D4 决策回放（HARNESS_SPEC §6.4）：decisions 账 → 可读事件流。

一行账=一次「她当时为什么说了 / 没说什么」。两个视图：
  timeline(rows) 按分钟分组，sent 与 vetoed/rejected/expired/deduped 成对读
  stats_text(rows) 账本体检表（角色×类型×结果 计数）

诚实边界：spec 原文的「重跑判断点出 verdict 差异表」这里不做——judges 的
LLM 裁决不确定，且账本只存抽出的 verdict、没存 LLM 原始 JSON，离线重放
无从比对（硬做=造个看着能跑其实假的东西）。判断点行为改动的回归面由
triage_check.py 真机增量筛查覆盖；本工具负责「一眼看清她为什么开这个口」。
"""
from __future__ import annotations

import datetime
import sqlite3


def _local(ts: str) -> str:
    try:
        return datetime.datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(ts)[:16]


def load(con: sqlite3.Connection, since: str = "", role: str = "",
         kind: str = "") -> list[dict]:
    sql = "SELECT * FROM decisions"
    cond: list[str] = []
    params: list = []
    if since:
        cond.append("ts>=?")
        params.append(since)
    if role:
        cond.append("role_id=?")
        params.append(role)
    if kind:
        cond.append("kind LIKE ?")
        params.append(f"%{kind}%")
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id"
    return [dict(r) for r in con.execute(sql, params).fetchall()]


_ORDER = {"sent": 0, "vetoed": 1, "rejected": 2, "expired": 3, "failed": 4, "deduped": 5}


def timeline(rows: list[dict]) -> str:
    """按分钟分组的事件流：同分钟内 sent 排前、否决类排后（读「说了什么、
    顺带压下了什么」）。"""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(_local(r["ts"]), []).append(r)  # %m-%d %H:%M（截 [:5] 会只剩日期）
    out: list[str] = []
    for minute in sorted(by):
        out.append(f"── {minute} ──")
        items = sorted(by[minute], key=lambda r: _ORDER.get(str(r["verdict"]), 9))
        for r in items:
            line = f"  {str(r['verdict']):<9} {r['kind']}"
            if r.get("reason"):
                line += f"  ‹{r['reason']}›"
            if r.get("digest") and r["verdict"] == "sent":
                line += f"  「{str(r['digest'])[:44]}」"
            out.append(line)
    return "\n".join(out)


def stats_text(rows: list[dict]) -> str:
    agg: dict[tuple, int] = {}
    for r in rows:
        key = (str(r["role_id"]), str(r["kind"]), str(r["verdict"]))
        agg[key] = agg.get(key, 0) + 1
    lines = [f"{'角色':<10}{'类型':<28}{'结果':<12}{'次数':>5}"]
    for (role, kind, verdict), n in sorted(agg.items()):
        lines.append(f"{role:<10}{kind:<28}{verdict:<12}{n:>5}")
    return "\n".join(lines)

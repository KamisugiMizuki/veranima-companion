"""真机导出筛查回归检查（HARNESS 精神的最小落地：把已付学费的症状类变成代码）。

用法:
    python scripts/triage_check.py exports/<date>/work.db [--since ISO] [--all]

- 默认只查 --since（或 triage_stamp.json 的 screened_through）之后的新数据——
  深读增量筛查的机器侧对表；
- --all 扫全历史（新写了一类检查时先用它校准已知命中）；
- 每项检查=一个真机付过学费的症状类，命中列原文+时刻，人（或下一轮 agent）
  决定是新病灶还是已知旧账。退出码非零=有任何命中（可进 preflight）。

检查表与 exports/triage_stamp.json 的 checked_classes 一一对应：加一类症状
=这里加一个函数 + stamp 记一笔，筛查配方见 skill
android-compose-ui/references/phone-data-triage.md。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sqlite3
import sys

STAMP = pathlib.Path(__file__).resolve().parents[1] / "exports" / "triage_stamp.json"


def _local(iso: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(str(iso))
        return (dt + datetime.timedelta(hours=8)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(iso)[:16]


# ---------- 症状类检查（每个=真机实锤过的一个病灶） ----------

def check_duplicates(con, since):
    """同文案 assistant 消息 ≥2 条/10min 内（09-05/06 苏醒播报复读实锤）。"""
    rows = con.execute(
        "SELECT content, created_at FROM messages WHERE role='assistant'"
        " AND created_at>? ORDER BY content, created_at", (since,)).fetchall()
    seen: dict[str, list[str]] = {}
    for c, t in rows:
        seen.setdefault(c, []).append(t)
    hits = []
    for c, ts in seen.items():
        if len(ts) < 2:
            continue
        # 复读的定义是短时间连发；隔天再说同一句=正常引用
        parsed = [datetime.datetime.fromisoformat(t) for t in ts]
        if (max(parsed) - min(parsed)) <= datetime.timedelta(minutes=240):
            hits.append((c[:60], [_local(t) for t in ts]))
    return hits


def check_internal_leak(con, since):
    """内部术语/协议残片出现在可见消息（08-28 raw JSON、08-31 依恋度/敬语刀实锤）。"""
    kw = ("依恋度", "敬语刀", "raw JSON", "不要输出思考过程", "PersonaBrief",
          "memory_candidates", "tension 值", "判断点）", "candidate_id")
    like = " OR ".join("content LIKE ?" for _ in kw)
    rows = con.execute(
        f"SELECT content, created_at FROM messages WHERE role='assistant'"
        f" AND created_at>? AND ({like})",
        (since, *[f"%{k}%" for k in kw])).fetchall()
    return [(c[:80], _local(t)) for c, t in rows]


def check_ledger_pollution(con, since):
    """张力判词混进记忆正文（09-06 实锤 60+ 条——写入侧已挡，此查漏网）。
    relational_tension_event 是设计内的账本归档行（09-07 增量首跑 6 条即此），
    病灶是判词被 digest 复述/进别的 kind——排除账本 kind 才是查漏网。"""
    rows = con.execute(
        "SELECT content, created_at FROM memories WHERE created_at>?"
        " AND (content LIKE ? OR content LIKE ?)"
        " AND meta NOT LIKE '%relational_tension_event%'",
        (since, "%回应了直接问题%", "%没有回应直接问题%")).fetchall()
    return [(c[:60], _local(t)) for c, t in rows]


def check_machine_moment(con, since):
    """动态发布报表腔/残句（moments._looks_machine/_looks_truncated 同款判据）。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from veranima.core.moments import _looks_machine, _looks_truncated
    rows = con.execute(
        "SELECT content, created_at, role_id FROM moments WHERE created_at>?",
        (since,)).fetchall()
    return [(f"{r}/{c[:50]}", _local(t)) for c, t, r in rows
            if _looks_machine(c) or _looks_truncated(c)]


def check_unbalanced_fabrication(con, since):
    """编造可证伪事实高发词（09-05『你们公司聚餐』对大四学生实锤）——只列候选，
    用户职业=上班族时这些是正常词，命中≠病灶，人读定夺。"""
    kw = ("你们公司", "你们老板", "你同事", "离城预案", "你班上加")
    like = " OR ".join("content LIKE ?" for _ in kw)
    rows = con.execute(
        f"SELECT content, created_at FROM messages WHERE role='assistant'"
        f" AND created_at>? AND ({like})", (since, *[f"%{k}%" for k in kw])).fetchall()
    return [(c[:80], _local(t)) for c, t in rows]


CHECKS = {
    "duplicate_broadcast": check_duplicates,
    "internal_leak": check_internal_leak,
    "ledger_pollution": check_ledger_pollution,
    "machine_moment": check_machine_moment,
    "fabrication_candidates": check_unbalanced_fabrication,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--since", default="")
    ap.add_argument("--all", action="store_true", help="扫全历史（校准用）")
    args = ap.parse_args()
    since = "0000" if args.all else (args.since or "")
    if not since and STAMP.exists():
        st = json.loads(STAMP.read_text(encoding="utf-8"))
        since = st.get("screened_through", "0000")
        missing = set(CHECKS) - set(st.get("classes") or [])
        if missing:
            print(f"注意：{sorted(missing)} 是上次盖章后新增的症状类——"
                  f"旧数据对它没有保证，先 --all 校准一次再回增量模式")
    if not since:
        since = "0000"
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    print(f"triage_check {args.db}  since={_local(since) if since != '0000' else '全历史'}")
    bad = 0
    for name, fn in CHECKS.items():
        try:
            hits = fn(con, since)
        except sqlite3.OperationalError as e:
            print(f"  {name}: SKIP ({e})")
            continue
        mark = "HIT" if hits else "ok "
        print(f"  [{mark}] {name}: {len(hits)}")
        for h in hits[:8]:
            print(f"        {h}")
        bad += len(hits)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

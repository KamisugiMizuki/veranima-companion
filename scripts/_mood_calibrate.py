"""USER_MOOD_SPEC §6 的 P0 离线校准（2026-09-20）。

拿真实导出库的只读副本跑「近况对照」判据，输出触发频率与触发窗口，供人工判读
误报；阈值以校准记录为准（docs/mind/audits/）。只读、不写库、不产出文件。

用法：
    python scripts/_mood_calibrate.py --db exports/phone-20260911/veranima.db
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import statistics

SHORT_ABS = 6          # 绝对短：<6 字
SHORT_RATIO = 0.5      # 相对短：低于基线 50%
RUN_MIN = 4            # 连续短句触发主干（草案值）
BASE_MIN = 20          # 基线太短不判（画像未成熟/本就话少）
SESSION_GAP_H = 3.0    # 同一会话：间隔 ≤3h
PUNCT = "！？!?。…~～"
EMOJI = "😀😄😁😊🙂😉😂🤣😭😢😔😞😩😫🥲❤️💔👍🙏"


def _len(text: str) -> int:
    """字数：不算空白，标点算（真人短句常带标点）。"""
    return len(re.sub(r"\s", "", text or ""))


def _signals(texts: list[str]) -> dict:
    joined = "".join(texts)
    n = max(1, len(joined))
    return {
        "punct": sum(joined.count(c) for c in PUNCT) / n,
        "emoji": sum(joined.count(c) for c in EMOJI) / n,
    }


def calibrate(db: str, *, run_min: int = RUN_MIN, verbose: int = 8,
              collapse_dupes: bool = True, min_distinct: int = 2,
              baseline: str = "mean") -> dict:
    """collapse_dupes/min_distinct 是 09-20 校准后加的两条收紧（见 docs/mind/audits）。

    首轮校准实锤：真实库里的「连发短句」绝大多数是**同一条消息重复发送**
    （程序化重试、功能测试）或早晚问候/确认词，不是低落——不去重的原始判据
    4/4 全是误报。收紧后判据仍只做「要不要看」的预筛，结论归 LLM。
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT role_id, content, created_at FROM messages WHERE role='user'"
        " ORDER BY role_id, created_at, id"
    ).fetchall()
    con.close()

    by_role: dict[str, list[dict]] = {}
    for r in rows:
        by_role.setdefault(str(r["role_id"] or ""), []).append(dict(r))

    out: dict[str, dict] = {}
    for role, msgs in by_role.items():
        lengths = [_len(m["content"]) for m in msgs]
        if not lengths:
            continue
        baseline_mean = statistics.fmean(lengths)
        baseline_med = statistics.median(lengths)
        # 稳健中心：均值被长文拉高（真机 30.6 vs 中位 12），会把中等长度误判成「短」
        base = baseline_med if baseline == "median" else baseline_mean
        base_signals = _signals([m["content"] for m in msgs])

        def is_short(text: str) -> bool:
            n = _len(text)
            return n < SHORT_ABS or n <= SHORT_RATIO * base

        sessions = 1
        triggers: list[dict] = []
        fired_in_session = False
        run = 0
        distinct: set[str] = set()
        prev_text = None
        for i, m in enumerate(msgs):
            gap_h = 0.0
            if i:
                try:
                    t0 = dt.datetime.fromisoformat(str(msgs[i - 1]["created_at"]))
                    t1 = dt.datetime.fromisoformat(str(m["created_at"]))
                    gap_h = (t1 - t0).total_seconds() / 3600.0
                except (TypeError, ValueError):
                    gap_h = 0.0
            if gap_h > SESSION_GAP_H:
                sessions += 1
                fired_in_session = False
                run = 0
            text = str(m["content"])
            if collapse_dupes and text == prev_text:
                continue                     # 同一条连发（重试/测试）不算新的一句
            prev_text = text
            if is_short(text):
                run += 1
                distinct.add(text.strip())
            else:
                run = 0
                distinct = set()
            if run < run_min or base < BASE_MIN or fired_in_session:
                continue
            if len(distinct) < min_distinct:
                continue                     # 全是同一句复读：不是"连着几句没力气"
            window = msgs[max(0, i - 5):i + 1]
            win_signals = _signals([w["content"] for w in window])
            triggers.append({
                "at": m["created_at"], "run": run,
                "lengths": [_len(w["content"]) for w in window],
                "punct_ratio": round(win_signals["punct"] / max(1e-9, base_signals["punct"]), 2),
                "emoji_ratio": round(win_signals["emoji"] / max(1e-9, base_signals["emoji"]), 2),
                "texts": [str(w["content"]) for w in window],
            })
            fired_in_session = True
        out[role] = {
            "messages": len(msgs), "baseline_mean": round(baseline_mean, 1),
            "baseline_median": baseline_med, "sessions": sessions,
            "triggers": len(triggers), "samples": triggers[:verbose],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-min", type=int, default=RUN_MIN)
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--baseline", choices=["mean", "median"], default="mean")
    args = ap.parse_args()
    report = calibrate(args.db, run_min=args.run_min, verbose=args.samples,
                       baseline=args.baseline)
    for role, data in report.items():
        print(f"\n=== role={role or '(无角色)'} ===")
        print(f"  消息 {data['messages']} 条 / 会话 {data['sessions']} 段 / "
              f"基线均值 {data['baseline_mean']} 字（中位 {data['baseline_median']}）")
        print(f"  触发 {data['triggers']} 次"
              f"（每会话 {data['triggers'] / max(1, data['sessions']):.2f} 次）")
        for s in data["samples"]:
            print(f"  -- {s['at']} 连短 {s['run']} 条 长度 {s['lengths']} "
                  f"标点×{s['punct_ratio']} 表情×{s['emoji_ratio']}")
            for t in s["texts"]:
                print(f"       | {t[:40]}")


if __name__ == "__main__":
    main()

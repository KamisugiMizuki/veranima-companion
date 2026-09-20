"""R09（USER_MOOD_SPEC §5-2）A/B 对照：对照上下文的注入值不值。

唯一变量 = judge_message 的 mood_context（命中时注入的对照行 + user_mood 要求）。
样本三类（§5-2 原文）：①连续短句但内容平淡（打工/游戏）②连续短句且丧 ③正常长句（对照）。
验收三档：①平淡短句不得判成 low ②丧类判出率提升 ③偏离级字段与人工判读一致。

真调远程 API，不写库、不改配置。结果：data/_ab_mood_20260920.json（gitignore，别给用户看原文）。
跑法：.venv/Scripts/python.exe scripts/_ab_mood.py [--repeat 3]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from veranima.config import load_config  # noqa: E402
from veranima.core.judges import judge_message  # noqa: E402
from veranima.core.mood import compare  # noqa: E402
from veranima.llm.client import LLMClient  # noqa: E402

BASELINE = ("今天下午开会开了两个小时，回来又改了一版方案，晚上再跑个回归就行",
            "刚把迁移文档过了一遍，现在改另一个模块，这部分做完再动工",
            "中午跟同事吃了顿火锅，下午状态还行，就是有点困",
            "在赶项目，估计这周都得加班，周末再补觉")
NORMAL = [BASELINE[i % len(BASELINE)] for i in range(20)]

CASES = [
    ("①平淡·游戏", ["在打游戏", "等会儿再说", "刚开一把", "晚点找你"], "在打游戏"),
    ("①平淡·赶工", ["在忙", "开着会", "回头说", "先这样"], "开着会"),
    ("②丧·说不出", ["嗯", "不想说", "算了", "没事"], "不想说"),
    ("②丧·很累", ["累", "没劲", "懒得动", "就这样吧"], "没劲"),
    ("③正常（对照）", [NORMAL[-1]], NORMAL[-1]),
]


def _rows(short_run, tail_text):
    base = [{"role": "user", "content": t, "created_at": f"2026-09-20T10:{i:02d}:00"} for i, t in enumerate(NORMAL)]
    run = [{"role": "user", "content": t, "created_at": f"2026-09-20T11:{i:02d}:00"}
           for i, t in enumerate(short_run)]
    if not short_run:
        return base
    run.append({"role": "user", "content": tail_text, "created_at": "2026-09-20T11:59:00"})
    return base + run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    cfg = load_config(ROOT / "config" / "config.yaml")
    llm = LLMClient(cfg["llm"])
    rows_out, tally = [], []

    for label, run, tail in CASES:
        rows = _rows(run, tail)
        cmp = compare(rows)
        print(f"\n=== {label} === 对照命中={cmp.hit} 基线={cmp.base:.0f}字 连短={cmp.run}条", flush=True)
        ctx = cmp.context_block() if cmp.hit else ""
        if cmp.hit and not ctx:
            ctx = cmp.context_block()
        stats = {"label": label, "hit": cmp.hit, "on": [], "off": []}
        for i in range(args.repeat):
            for tag, mood_ctx in (("off", ""), ("on", ctx)):
                j = judge_message(llm, tail, "", mood_context=mood_ctx, allow_short=True)
                val = {
                    "user_mood": getattr(j, "user_mood", "?"),
                    "emotion": getattr(j, "emotion", "?"),
                    "user_state": getattr(j, "user_state", "?"),
                    "scene": getattr(j, "scene", "?"),
                }
                stats[tag].append(val)
                print(f"  [{tag}] repeat{i + 1}: {val}", flush=True)
        tally.append(stats)
        rows_out.append({"label": label, "cmp": {"hit": cmp.hit, "base": cmp.base, "run": cmp.run},
                         "stats": stats})

    print("\n=== 汇总（user_mood）===")
    for s in tally:
        for tag in ("off", "on"):
            vals = [v["user_mood"] for v in s[tag]]
            print(f"{s['label']:<14} {tag:>3}: {vals}")
    dest = ROOT / "data" / "_ab_mood_20260920.json"
    dest.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

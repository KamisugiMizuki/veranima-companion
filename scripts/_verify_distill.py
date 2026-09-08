"""实机验证：真库副本上跑一遍存量蒸馏回填（2026-09-08 记忆粒度修复）。

走真实生产路径 `Agent.maybe_distill_backfill`（含假设句丢弃、版本链、meta 标记、
幂等），不碰真库：先复制到临时文件再动。

跑法：.venv/Scripts/python -X utf8 scripts/_verify_distill.py [db]
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from veranima.app import create_agent  # noqa: E402
from veranima.config import load_config  # noqa: E402
from veranima.memory.store import MemoryStore  # noqa: E402

LAYERS = ("semantic", "episodic", "procedural")


def snapshot(store: MemoryStore) -> dict[int, str]:
    out: dict[int, str] = {}
    for layer in LAYERS:
        for e in store.list_layer(layer, limit=500):
            out[e.id] = e.content
    return out


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT / "exports" / "mumu_20260908.db"
    if not src.exists():
        print(f"真库不存在：{src}")
        return
    tmp = Path(tempfile.gettempdir()) / "veranima_distill_check.db"
    shutil.copy2(src, tmp)

    cfg = load_config()
    mem_cfg = {
        **cfg.get("memory", {}),
        "host": cfg.get("llm", {}).get("base_url", ""),
        "root": str(PROJECT),
    }
    store = MemoryStore(db_path=str(tmp), config=mem_cfg, llm_config=cfg.get("llm", {}))
    agent = create_agent(cfg, memory=store)

    before = snapshot(store)
    print(f"库副本={tmp}  回填前条目={len(before)}\n")
    result = agent.maybe_distill_backfill(limit=500)
    after = snapshot(store)

    for mid, old in before.items():
        if mid not in after:
            print(f"#{mid} 删除 ← {old[:60]}")
        elif after[mid] != old:
            print(f"#{mid} 改写\n    前：{old[:80]}\n    后：{after[mid]}")
    print(f"\n账：{result}")
    print(f"回填后条目={len(after)}（前 {len(before)}）")

    again = agent.maybe_distill_backfill(limit=500)
    print(f"幂等重跑：{again}")


if __name__ == "__main__":
    main()

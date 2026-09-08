"""A2 验证：judges 的 emotion 字段在真实链路里到底产出什么（PAD 恒 0.5 的供血源）。

跑法：.venv/Scripts/python scripts/_audit_emotion.py
不写 DB、不改代码、不动配置，只调 LLM。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from veranima.config import load_config  # noqa: E402
from veranima.core.judges import judge_message  # noqa: E402
from veranima.llm.client import LLMClient  # noqa: E402

TESTS = [
    ("明显开心", "今天升职了！哈哈哈哈"),
    ("明显烦躁", "烦死了，项目又延期了"),
    ("焦虑", "睡不着，明天还要汇报"),
    ("平静陈述", "刚把文档过了一遍。"),
]


def main() -> int:
    cfg = load_config(ROOT / "config" / "config.yaml")
    llm = LLMClient(cfg["llm"])
    for label, text in TESTS:
        try:
            j = judge_message(llm, text)
            print(f"{label:6s} emotion={j.emotion!r} tease={j.tease} tension={j.tension!r} "
                  f"conflict={j.conflict!r} memory={j.memory_kind!r}")
        except Exception as e:
            print(f"{label:6s} FAIL {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

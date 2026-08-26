from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from pathlib import Path

from veranima.app import create_agent
from veranima.config import load_config
from veranima.core.reply import is_failure_fallback_reply, is_internal_reply
from veranima.memory.store import MemoryStore

PROJECT = Path(__file__).resolve().parent
PROMPTS = [
    "今天想聊点轻松的，讲讲你最近在忙什么。",
    "我最近在整理一个后端项目，感觉边界有点乱。",
    "你会怎么判断一个模块是不是过度设计？",
    "我还在考虑是否要加入缓存。",
    "先不聊技术了，你现在心情怎么样？",
    "你刚才说的那个判断标准，能展开一点吗？",
    "我觉得自己有时候会把事情想复杂。",
    "如果只保留一个检查步骤，你会留什么？",
    "我有点累，但还不想睡。",
    "你会建议我今天停在这里吗？",
    "我明天还要继续处理这个项目。",
    "你能记住我比较在意边界清晰这件事吗？",
    "说说你自己的看法，不要只顺着我。",
    "如果我的方案有问题，直接指出来。",
    "今天先到这里，之后再继续。",
    "我刚才又想到一个新的拆分方式。",
    "你觉得这个想法和前面的结论冲突吗？",
    "不用太长，给我一个明确判断。",
    "谢谢，最后再说一句你自己的话。",
    "这轮对话你觉得最重要的部分是什么？",
]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="veranima-real-20-") as temp:
        root = Path(temp)
        cfg = copy.deepcopy(load_config())
        cfg["root"] = str(root)
        cfg["character_card"] = str(PROJECT / "characters" / "zima" / "character.json")
        cfg.setdefault("memory", {})["db_path"] = str(root / "conversation.db")
        cfg.setdefault("search", {})["enabled"] = False
        cfg.setdefault("tasks", {})["enabled"] = False
        agent = create_agent(cfg)
        rows = []
        for index, prompt in enumerate(PROMPTS, 1):
            result = agent.handle(prompt, channel="im")
            visible = str(result.reply or "").strip()
            stored = agent.memory.recent_messages(limit=1)[0]
            problems = []
            if not visible:
                problems.append("empty_visible_reply")
            if is_failure_fallback_reply(visible):
                problems.append("fallback_reply")
            if is_internal_reply(visible):
                problems.append("internal_reply")
            if any(token in visible for token in ("plan_id", "item_id", "source_anchor", "truth_class", "candidate_id")):
                problems.append("protocol_leak")
            if stored.get("role") != "assistant" or stored.get("content") != visible:
                problems.append("persistence_mismatch")
            rows.append({"turn": index, "chars": len(visible), "problems": problems})
            if problems:
                print(json.dumps({"ok": False, "turn": index, "rows": rows}, ensure_ascii=False))
                return 1
        reopened = MemoryStore(db_path=str(root / "conversation.db"), config=cfg.get("memory", {}), provider=agent.memory.provider)
        persisted = reopened.recent_messages(limit=50)
        assistant_count = sum(row.get("role") == "assistant" for row in persisted)
        reopened.con.close()
        agent.memory.con.close()
        print(json.dumps({
            "ok": True,
            "turns": len(rows),
            "assistant_rows_after_reopen": assistant_count,
            "min_reply_chars": min(row["chars"] for row in rows),
            "max_reply_chars": max(row["chars"] for row in rows),
            "rows": rows,
        }, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())

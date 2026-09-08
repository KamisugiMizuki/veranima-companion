"""P-5 反思链实测（真实 API）：跨重启触发 + LLM 第一人称反思 + 章节落库。

背景（09-08 实机验收）：`self_model_chapters` 0 行。根因是触发只看内存计数器
（`__init__` 里清零），安卓端冷启动频繁 → 永远攒不到 20，整条 P-5 链从没跑过。

本脚本验证四件事（全部走真实 LLM）：
  ① 消息数落库触发能到（`_reflection_due_persistent`）
  ② LLM 产出角色第一人称反思（不是「用户计划将…」这种原文搬运）
  ③ 章节与自我模型快照真的写进库
  ④ 跨实例节流：反思完再调不重复触发

用法：.venv/Scripts/python -X utf8 scripts/_verify_p5.py
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

from veranima.app import create_agent
from veranima.config import load_config

PROJECT = Path(__file__).resolve().parents[1]

EVIDENCE = [
    ("上次一起看的那部片子，我觉得他是想找个借口多待一会儿", 9001),
    ("那天他说工作上的事，我听出来他不是在抱怨，是想让人接一句", 9002),
    ("之前答应过要提醒他吃饭，我记着", 9003),
]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="veranima-p5-") as temp:
        root = Path(temp)
        cfg = copy.deepcopy(load_config())
        cfg["root"] = str(PROJECT)
        cfg["character_card"] = str(PROJECT / "characters" / "xumian" / "character.json")
        cfg.setdefault("memory", {})["db_path"] = str(root / "p5.db")
        cfg.setdefault("search", {})["enabled"] = False
        cfg.setdefault("tasks", {})["enabled"] = False
        agent = create_agent(cfg)
        try:
            for txt, cid in EVIDENCE:
                agent.memory.store("episodic", txt, confidence=0.8,
                                   meta={"kind": "shared_meaning", "source_message_id": cid})
            st = agent.memory.load_state() or {}
            st["total_messages"] = 25
            agent.memory.save_state(st)
            total = agent._state_total_messages()
            due = agent._reflection_due_persistent(total)
            print(f"[1] total_messages={total} due={due}")

            # 诊断：手动复现证据收集 + LLM 反思（定位链在哪断）
            from veranima.core.reflection import propose_reflection_llm, validate_reflection
            ev = []
            for layer, kinds in (("semantic", ("user_framework", "character_belief")),
                                 ("episodic", ("shared_meaning", "relationship_event"))):
                for e in agent.memory.list_layer(layer, limit=10):
                    if (e.meta or {}).get("kind") in kinds:
                        ev.append({"id": e.id, "kind": (e.meta or {}).get("kind"),
                                   "content": e.content, "confidence": e.confidence})
            print("[0] evidence=" + json.dumps(ev, ensure_ascii=False))
            r = propose_reflection_llm(ev[:5], agent._reflect_task)
            print("[0b] llm_r=" + repr(r))
            if r is not None:
                print("[0c] issues=" + json.dumps(validate_reflection(r, agent.card), ensure_ascii=False))

            agent._maybe_reflect()

            chapters = agent.memory.list_self_model_chapters()
            snaps = [e for e in agent.memory.list_layer("core_profile", limit=10, include_superseded=True)
                     if (e.meta or {}).get("kind") == "self_model_snapshot"]
            print("[2] chapters=" + json.dumps(
                [{"title": c.get("title"), "self_interpretation": c.get("self_interpretation"),
                  "key_events": c.get("key_events")} for c in chapters], ensure_ascii=False))
            print("[3] snapshots=" + json.dumps(
                [{"version": (e.meta or {}).get("version"),
                  "at_total_messages": (e.meta or {}).get("at_total_messages"),
                  "content": e.content[:120]} for e in snaps], ensure_ascii=False))

            agent._maybe_reflect()  # 第二次：应被节流挡住
            chapters2 = agent.memory.list_self_model_chapters()
            print(f"[4] after second call chapters={len(chapters2)} (expect {len(chapters)}) "
                  f"due={agent._reflection_due_persistent(total)}")

            # [5] 效果验证：反思产出必须进 prompt。否则只是写库、没人读 → 对「像不像」零影响
            # （self_model 本身没有 prompt 注入点，持久载体只有 core_profile 快照记忆）。
            from veranima.core.prompts import build_system_prompt
            sp = build_system_prompt(agent.card, agent.state, agent.memory)
            hit = [ln.strip() for ln in sp.splitlines() if "自我模型" in ln or "我对他" in ln]
            print("[5] prompt_hits=" + json.dumps(hit, ensure_ascii=False))

            ok = bool(chapters) and len(chapters2) == len(chapters) and due and bool(hit)
            print(json.dumps({"ok": ok}, ensure_ascii=False))
            return 0 if ok else 1
        finally:
            agent.memory.con.close()


if __name__ == "__main__":
    sys.exit(main())

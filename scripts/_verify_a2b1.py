"""A2/B1 实测：真实链路 + 临时 DB（不碰真实库、不改配置）。

A2（PAD 情绪）：真实 API 跑 3 轮 handle()，看 valence/arousal 是否起伏、
    last_cause 是否更新、跨实例是否持久化。
B1（第三人称）：mind_threads 放一条 origin=user 的第三人称 topic，看**两个出口**
    是否还吐「用户」——prompt_block（进 system 提示）与 spoken（直接发出去）。

跑法：.venv/Scripts/python -X utf8 scripts/_verify_a2b1.py
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from veranima.app import create_agent  # noqa: E402
from veranima.config import load_config  # noqa: E402

THIRD_PERSON_TOPIC = "用户正在赶毕设改稿"

ROUNDS = [
    ("开心", "今天升职了！哈哈哈哈"),
    ("烦躁", "烦死了，项目又延期了"),
    ("焦虑", "睡不着，明天还要汇报"),
]


def check_b1(agent) -> dict:
    agent.memory.thread_add(agent.threads.role, THIRD_PERSON_TOPIC, "user", intensity=0.9)
    rows = agent.threads.top()
    spoken = agent.threads.spoken(rows[0]) if rows else ""
    block = agent.threads.prompt_block()
    return {
        "topic_written": THIRD_PERSON_TOPIC,
        "prompt_block_leaks": "用户" in block,
        "spoken_leaks": "用户" in spoken,
        "prompt_block": block.split("\n")[0][:110],
        "spoken": spoken,
    }


def check_a2(agent) -> list[dict]:
    out = []
    for label, text in ROUNDS:
        before = [agent.state.valence, agent.state.arousal, agent.state.last_cause]
        res = agent.handle(text, channel="im")
        after = [agent.state.valence, agent.state.arousal, agent.state.last_cause]
        out.append({
            "label": label,
            "pad_before": [round(x, 3) for x in before[:2]],
            "pad_after": [round(x, 3) for x in after[:2]],
            "moved": before[:2] != after[:2],
            "last_cause": after[2],
            "affect_block": agent._affect_block()[:70],
            "reply": (res.reply or "")[:70],
        })
    return out


def main() -> int:
    b1_only = "--b1" in sys.argv
    with tempfile.TemporaryDirectory(prefix="veranima-a2b1-") as temp:
        cfg = copy.deepcopy(load_config())
        cfg["root"] = str(PROJECT)
        cfg["character_card"] = str(PROJECT / "characters" / "xumian" / "character.json")
        cfg.setdefault("memory", {})["db_path"] = str(Path(temp) / "verify.db")
        cfg.setdefault("search", {})["enabled"] = False
        cfg.setdefault("tasks", {})["enabled"] = False
        cfg.setdefault("virtual_schedule", {})["enabled"] = False

        llm_cfg = cfg.get("llm") or {}
        print(f"model={llm_cfg.get('model')} base_url={llm_cfg.get('base_url')}")

        agent = create_agent(cfg)
        try:
            print(f"card={agent.card.name} role_key={agent.role_key!r}")
            print("\n=== B1 第三人称（两出口）===")
            print(json.dumps(check_b1(agent), ensure_ascii=False, indent=1))
            if b1_only:
                return 0
            print("\n=== A2 PAD（真实 handle 三轮）===")
            for row in check_a2(agent):
                print(json.dumps(row, ensure_ascii=False))
        finally:
            agent.memory.con.close()

        agent2 = create_agent(cfg)
        try:
            print("\n=== A2 跨实例持久化 ===")
            print(json.dumps({
                "valence": round(agent2.state.valence, 3),
                "arousal": round(agent2.state.arousal, 3),
                "last_cause": agent2.state.last_cause,
            }, ensure_ascii=False))
        finally:
            agent2.memory.con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

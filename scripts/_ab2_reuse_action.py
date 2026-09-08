"""实验 2：真实完整 prompt 下，【回用动作】这条「强制使用」指令该不该留。

唯一变量 = system prompt 里【回用动作】那一行（真实链路由 reuse_action 参数生成）。
其余全部一致：真实 Agent 构造的完整 prompt（19 块，含角色卡/记忆/风格/承诺）。
不写 DB、不改代码、不动配置，只调 LLM。

跑法：.venv/Scripts/python scripts/_ab2_reuse_action.py
结果：data/_ab2_reuse_action.json（含答案，别给用户看这个文件）
"""
from __future__ import annotations

import json
import random
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from veranima.config import load_config  # noqa: E402
from veranima.core.agent import Agent  # noqa: E402
from veranima.core.character import CharacterCard  # noqa: E402
from veranima.core.prompts import build_system_prompt  # noqa: E402
from veranima.llm.client import LLMClient  # noqa: E402
from veranima.memory.store import MemoryStore  # noqa: E402

TESTS = [
    ("拖延报备", "一点没学。但也不知道究竟干了什么。反正就是熬到了这个点。我决定先睡觉，明天起床看心情，可能猛学一天，也有可能看个五六个小时就开始摆烂。总之明天肯定要继续学了。"),
    ("点饭", "搞点吃的，吃完开看"),
    ("问安排", "好吧好吧我错了。今天有什么安排？"),
    ("说不上来的烦", "有点烦，说不上来为什么。"),
    ("工作进度", "刚把迁移文档过了一遍，现在改另一个模块，这部分做完再动工。"),
    ("问角色在干嘛", "你在干嘛"),
]

SEED = 20260908
# state 快照来自旧角色，与许眠卡（交往三年/异地一年半）不符，两边统一修正
REL_FIX = "【关系】你们处于稳定相伴期：自然如常，安静陪伴；不必刻意表现。"


def extract_text(raw: str) -> str:
    t = raw.strip()
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            segs = obj.get("segments") or []
            parts = [str(s.get("text", "")).strip() for s in segs if isinstance(s, dict)]
            parts = [p for p in parts if p]
            if parts:
                return "\n".join(parts)
        except json.JSONDecodeError:
            pass
    return t


def build_prompts() -> tuple[dict, str, str]:
    cfg = load_config(ROOT / "config" / "config.yaml")

    src = ROOT / "data" / "veranima.db"
    tmp = ROOT / "data" / "_ab2.db"
    for suf in ("", "-wal", "-shm"):
        s = Path(str(src) + suf)
        if s.exists():
            shutil.copy2(s, Path(str(tmp) + suf))

    mem = MemoryStore(db_path=str(tmp), config=cfg.get("memory") or {},
                      llm_config=cfg.get("llm") or {})
    card = CharacterCard.from_file(ROOT / "characters" / "xumian" / "character.json")
    agent = Agent(card, mem, LLMClient(cfg["llm"]), config=cfg)

    extra = []
    for fn in (lambda: agent.style.to_prompt_block(channel="im"),
               lambda: agent.mirror.to_prompt_block(),
               lambda: agent.promises.to_prompt_block(),
               lambda: agent._affect_block(),
               lambda: agent.threads.prompt_block()):
        try:
            b = fn()
            if b:
                extra.append(b)
        except Exception as e:
            print(f"[warn] extra block failed: {type(e).__name__}: {e}")

    sp = build_system_prompt(
        agent.card, agent.state, agent.memory,
        relationship=agent.relationship,
        reuse_action="remember",
        channel="im",
        extra_blocks=extra,
    )
    sp = re.sub(r"【关系】[^\n]*", REL_FIX, sp, count=1)

    ver_c = sp                                    # C：现状（含【回用动作】）
    ver_d = re.sub(r"【回用动作】[^\n]*\n?", "", sp, count=1)   # D：只删那一行
    assert "【回用动作】" in ver_c and "【回用动作】" not in ver_d
    print(f"[prompt] C={len(ver_c)} chars, D={len(ver_d)} chars, 差 {len(ver_c)-len(ver_d)}")
    return cfg, ver_c, ver_d


def main() -> int:
    cfg, sys_c, sys_d = build_prompts()
    llm = LLMClient(cfg["llm"])
    rng = random.Random(SEED)

    out = []
    for label, user_text in TESTS:
        pair = {}
        for tag, system in (("C", sys_c), ("D", sys_d)):
            raw = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
                max_tokens=1024,
                temperature=0.8,
            )
            pair[tag] = extract_text(raw)
            print(f"[{label}] {tag} done", flush=True)
        order = ["C", "D"]
        rng.shuffle(order)
        out.append({
            "label": label,
            "user": user_text,
            "shown": {
                "①": {"version": order[0], "text": pair[order[0]]},
                "②": {"version": order[1], "text": pair[order[1]]},
            },
        })

    dest = ROOT / "data" / "_ab2_reuse_action.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

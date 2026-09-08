"""实验 2 探针：用真实 Agent 构造一次 system prompt，看块结构够不够做 C/D 对照。

只读：DB 副本 + 角色卡，不写生产库、不改代码。
跑法：.venv/Scripts/python scripts/_ab2_probe.py
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from veranima.config import load_config                      # noqa: E402
from veranima.core.agent import Agent                        # noqa: E402
from veranima.core.character import CharacterCard            # noqa: E402
from veranima.core.prompts import build_system_prompt        # noqa: E402
from veranima.llm.client import LLMClient                    # noqa: E402
from veranima.memory.store import MemoryStore                # noqa: E402

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
llm = LLMClient(cfg["llm"])
agent = Agent(card, mem, llm, config=cfg)

extra = []
for name, fn in (
    ("style", lambda: agent.style.to_prompt_block(channel="im")),
    ("mirror", lambda: agent.mirror.to_prompt_block()),
    ("promises", lambda: agent.promises.to_prompt_block()),
    ("affect", lambda: agent._affect_block()),
    ("threads", lambda: agent.threads.prompt_block()),
):
    try:
        b = fn()
        if b:
            extra.append(b)
            print(f"[extra:{name}] {len(b)} chars")
    except Exception as e:
        print(f"[extra:{name}] FAILED {type(e).__name__}: {e}")

sp = build_system_prompt(
    agent.card, agent.state, agent.memory,
    relationship=agent.relationship,
    reuse_action="remember",
    channel="im",
    extra_blocks=extra,
)
print(f"\nsystem prompt = {len(sp)} chars\n")
for line in sp.split("\n"):
    if line.startswith("【") or line.startswith("#") or line.startswith("- "):
        print("   ", line[:70])
print("\n--- 含回用动作:", "【回用动作】" in sp)
print("--- 含理解用户:", "【理解用户】" in sp)
print("--- 含共同意义:", "【共同意义】" in sp)
print("--- 含角色观点:", "【角色观点】" in sp)
print("--- 记忆块:", [l[:14] for l in sp.split("\n") if l.startswith("【")][:20])

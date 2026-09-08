"""A/B 对照实验：CHANNEL_CONTEXT['im'] 的「表演规则」该不该留。

唯一变量 = system prompt 里那一段。其余（角色卡、输出格式、温度、输入）完全一致。
不写 DB、不改代码、不动配置，只调 LLM。

跑法：.venv/Scripts/python scripts/_ab_channel_context.py
结果：data/_ab_channel_context.json（含答案，别给用户看这个文件）
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from veranima.config import load_config  # noqa: E402
from veranima.core.character import CharacterCard  # noqa: E402
from veranima.core.prompts import CHANNEL_CONTEXT  # noqa: E402
from veranima.llm.client import LLMClient  # noqa: E402

# 输出格式两版一致（真实链路就是这么要求的），确保差异只来自 CHANNEL_CONTEXT
OUT_FMT = (
    "\n\n【输出格式·文字聊天】你的回复必须只输出 JSON，不要输出思考过程、"
    "分析步骤、草稿、规则核对或 Markdown。\n"
    '{"segments":[{"text":"...","tone":"语气标签"}]}'
)

# B 版：只保留「媒介是什么」这个事实，删掉全部表演规则
MINIMAL_CONTEXT = "【当前场景】你正在用手机跟对方打字聊天。"

TESTS = [
    ("拖延报备", "一点没学。但也不知道究竟干了什么。反正就是熬到了这个点。我决定先睡觉，明天起床看心情，可能猛学一天，也有可能看个五六个小时就开始摆烂。总之明天肯定要继续学了。"),
    ("点饭", "搞点吃的，吃完开看"),
    ("问安排", "好吧好吧我错了。今天有什么安排？"),
    ("说不上来的烦", "有点烦，说不上来为什么。"),
    ("工作进度", "刚把迁移文档过了一遍，现在改另一个模块，这部分做完再动工。"),
    ("问角色在干嘛", "你在干嘛"),
]

SEED = 20260908


def extract_text(raw: str) -> str:
    """从模型输出里抠出可读文本（JSON 可能带 fence / 前后杂字）。"""
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


def main() -> int:
    cfg = load_config(ROOT / "config" / "config.yaml")
    llm = LLMClient(cfg["llm"])
    card = CharacterCard.from_file(ROOT / "characters" / "xumian" / "character.json")
    base = card.to_system_prompt()

    sys_a = base + "\n\n" + CHANNEL_CONTEXT["im"] + OUT_FMT          # 现状
    sys_b = base + "\n\n" + MINIMAL_CONTEXT + OUT_FMT                # 减法

    rng = random.Random(SEED)
    out = []
    for label, user_text in TESTS:
        pair = {}
        for tag, system in (("A", sys_a), ("B", sys_b)):
            raw = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
                max_tokens=1024,
                temperature=0.8,
            )
            pair[tag] = extract_text(raw)
            print(f"[{label}] {tag} done", flush=True)
        order = ["A", "B"]
        rng.shuffle(order)
        out.append({
            "label": label,
            "user": user_text,
            "shown": {  # ① / ② 对应哪个版本
                "①": {"version": order[0], "text": pair[order[0]]},
                "②": {"version": order[1], "text": pair[order[1]]},
            },
        })

    dest = ROOT / "data" / "_ab_channel_context.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""月度回顾（MVP3）：周期性生成"我们一起走过的日子"小结。

素材来源：记忆层（episodic 共同经历 / semantic 用户偏好 / 消息统计），
由 LLM 以角色口吻写成一段有温度的回顾。
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """你是{name}。用户在请你们一起回顾这段时间的相处。请以你自己的口吻，写一段 150-250 字的回顾：
- 从下面的记忆中挑 2-3 件具体的事提一提（这是你们共同经历过的，是你"记得"的）
- 语气真诚自然，像朋友聊天一样，不要写成工作报告
- 可以有温度，但不要肉麻；可以有一点点你自己的感受
- **绝对不要编造记忆里没有的事**。如果下面的记忆为空或很少，就坦诚地说"我们还在慢慢了解彼此"，不要虚构任何共同经历、对话或细节
- 不要加具体日期数字

关于用户的记忆：
{memories}

最近的相处情况：{stats}"""


class MonthlyReview:
    """月度回顾生成器：检索记忆 → LLM 生成 → 返回文本。"""

    def __init__(self, memory: MemoryStore, llm=None):
        self.memory = memory
        self.llm = llm

    def collect_materials(self, limit: int = 12) -> dict:
        """收集回顾素材：episodic + semantic 记忆 + 消息统计。"""
        eps = self.memory.list_layer("episodic", limit=limit)
        sem = self.memory.list_layer("semantic", limit=limit)
        stats = self.memory.stats()
        mem_lines = []
        for e in eps[:6]:
            mem_lines.append(f"- 经历：{e.content[:80]}")
        for e in sem[:6]:
            mem_lines.append(f"- 了解：{e.content[:80]}")
        if not mem_lines:
            mem_lines.append("- （这段时间的记忆还不多，可以坦诚地说还在慢慢了解对方）")
        return {
            "memories": "\n".join(mem_lines),
            "stats": f"共 {stats.get('messages', 0)} 条消息",
            # 有效素材条数（不含占位）——代码层判断能否让 LLM 写（防编造）
            "material_count": len(eps) + len(sem),
        }

    def generate(self, name: str = "小V") -> str:
        """生成回顾文本。记忆素材过少或 LLM 不可用时返回降级文案（防编造）。"""
        materials = self.collect_materials()
        # 代码层拦截：有效素材 < 2 条时不调 LLM（8B 对'不要编造'遵循弱，直接给坦诚文案）
        if materials["material_count"] < 2:
            return self._fallback(materials)
        if self.llm is None or not getattr(self.llm, "is_available", lambda: True)():
            return self._fallback(materials)
        prompt = REVIEW_PROMPT.format(
            name=name,
            memories=materials["memories"],
            stats=materials["stats"],
        )
        try:
            reply = self.llm.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            return reply.strip()
        except Exception as e:
            logger.warning("monthly review generation failed: %s", e)
            return self._fallback(materials)

    @staticmethod
    def _fallback(materials: dict) -> str:
        """LLM 不可用/素材不足时的降级回顾（坦诚，不编造）。"""
        if materials["material_count"] == 0:
            return (
                "这段时间我们还在慢慢了解彼此，记忆也在一点点积累。\n"
                "以后的日子，慢慢一起过吧。"
            )
        return (
            "这段时间我们一起聊了一些。\n"
            f"{materials['memories']}\n"
            f"{materials['stats']}。\n"
            "以后的日子，也慢慢一起过吧。"
        )

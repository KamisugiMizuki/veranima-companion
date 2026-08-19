"""R0 能力匹配层（R0_SPEC 1.capabilities）：判定角色「能不能做」，触发「懂但不多」模式。

角色卡 veranima.capabilities：{擅长: [...], 略知: [...], 完全不懂: [...]}
- 擅长话题 → 正常展开
- 略知话题 → 「懂但不多」话术（四型：好奇/共情/关联/走神）
- 完全不懂 → 坦诚不会（不装懂）
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 「懂但不多」话术库（DESIGN 4.3：每型 ≥3 种，随角色卡 tone 微调；此处为通用模板池）
CURIOUS = [  # 好奇型
    "这个我懂一点皮毛……你是怎么入坑的？",
    "我不太熟，但听起来很有意思，多讲讲？",
    "略懂略懂，不过细节都是你告诉我的。",
]
EMPATHETIC = [  # 共情型
    "虽然我不太懂，但能感觉到这事对你挺重要的。",
    "我不太明白具体门道，不过你开心就好。",
    "听你说得这么起劲，我也跟着高兴。",
]
RELATED = [  # 关联型
    "这个我不太熟，不过有点像我以前接触过的那个……",
    "不懂这个，但你上次说的那件事跟这有点像？",
    "我这边一知半解，倒是想起个差不多的事。",
]
WANDER = [  # 走神型
    "啊……这个我确实不懂。（思绪飘了）你继续。",
    "完全没概念，不过你说的每个字我都听着呢。",
    "我不懂，但感觉你在发光。（认真听）",
]

DONT_KNOW = [
    "这个我是真不懂，不装了。",
    "完全不会，别指望我，哈哈。",
    "这个我不清楚。",
]


def capability_level(card, topic: str) -> str:
    """判定角色对话题的熟悉度：擅长 / 略知 / 完全不懂 / 未知。

    低配关键词匹配（DESIGN 4.3 复用：KISS 规则表优先）。
    """
    ver = getattr(card, "veranima", None) or {}
    caps = ver.get("capabilities", {}) or {}
    for kw in caps.get("擅长", []):
        if kw and kw in topic:
            return "擅长"
    for kw in caps.get("略知", []):
        if kw and kw in topic:
            return "略知"
    for kw in caps.get("完全不懂", []):
        if kw and kw in topic:
            return "完全不懂"
    return "未知"


def dont_know_much_line(style: str = "好奇", rng=None) -> str:
    """按话术型返回一句「懂但不多」回应。"""
    import random
    pool = {"好奇": CURIOUS, "共情": EMPATHETIC, "关联": RELATED, "走神": WANDER}.get(style, CURIOUS)
    return (rng or random).choice(pool)


def dont_know_line(rng=None) -> str:
    """坦诚不会的回应。"""
    import random
    return (rng or random).choice(DONT_KNOW)

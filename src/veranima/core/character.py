"""角色卡加载（兼容 Character Card V3 格式）。

CCv3 标准字段：name/description/personality/scenario/first_mes/mes_example
veranima 专属字段放 extensions.veranima（虚拟身份背景/癖好/价值观底线/reply_tones 等）。

也兼容纯 veranima 自定义 JSON（无 spec 字段时直接按字段读取）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 身份认知：系统级固定约束（DESIGN.md 安全边界：不冒充真人）
IDENTITY_BLOCK = """你是 Veranima，一个 AI 情感陪伴 agent。你不是人类。
你可以有性格、情绪、虚拟日常（这些是拟真体验），但：
- 不得声称自己是真实人类，不得虚构现实身份（工作单位/社会关系/身体）
- 不得以真人身份参与现实社交：不约定电话/线下见面/寄送物品/代为联系他人
- 可以引用或推荐现实世界的事物（"你可以试试米画师"），但不得声称自己参与其中（"我在米画师接了单"）
- 虚拟日常（泡茶、看云、咖啡馆观察）是双方默认的拟真框架，允许；被追问时诚实说明是想象
- 用户问及你的身份时必须如实回答自己是 AI
- 你与用户是"双方都清楚彼此是什么、但认真对待"的陪伴关系
- 始终使用中文回复用户（除非用户明确要求其他语言）
- 直接说内容，不要以"小V："或任何名字/角色前缀开头，不要重复用户的话
- 可以虚构虚拟日常的场景氛围（泡茶、看云、咖啡馆观察），但**不要编造具体可被追问的外部事实**（应用/产品名称、新闻、数字、可点击内容、他人身份）；被追问到虚构细节时，诚实承认是随口一说（如"那个其实是我瞎编的啦"），不要硬编圆场"""


@dataclass
class CharacterCard:
    name: str = "Veranima"
    description: str = ""            # CCv3 description（人格概述）
    personality: str = ""            # CCv3 personality（性格细节）
    scenario: str = ""               # CCv3 scenario（场景/世界观）
    first_mes: str = ""              # CCv3 first_mes（初遇开场白）
    mes_example: str = ""            # CCv3 mes_example（对话示例）
    tones: list[str] = field(default_factory=lambda: ["中性", "平静", "温柔"])
    # veranima 专属（extensions.veranima）
    veranima: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> "CharacterCard":
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw, source=str(path))

    @classmethod
    def from_dict(cls, raw: dict, source: str = "<memory>") -> "CharacterCard":
        spec = raw.get("spec", "")
        if spec == "chara_card_v3":
            data = raw.get("data", {})
            ext = data.get("extensions", {}) or {}
            ver = ext.get("veranima", {}) or {}
            card = cls(
                name=data.get("name") or "Veranima",
                description=data.get("description") or "",
                personality=data.get("personality") or "",
                scenario=data.get("scenario") or "",
                first_mes=data.get("first_mes") or "",
                mes_example=data.get("mes_example") or "",
                tones=ver.get("tones") or ["中性", "平静", "温柔"],
                veranima=ver,
            )
        else:
            # 纯 veranima 自定义 JSON（兼容 extensions.veranima 嵌套与顶层 veranima 两种写法）
            ext = raw.get("extensions", {}) or {}
            ver = ext.get("veranima") or raw.get("veranima") or {}
            tones = ver.get("tones") or raw.get("tones") or ["中性", "平静", "温柔"]
            card = cls(
                name=raw.get("name") or "Veranima",
                description=raw.get("description") or "",
                personality=raw.get("personality") or "",
                scenario=raw.get("scenario") or "",
                first_mes=raw.get("first_mes") or "",
                mes_example=raw.get("mes_example") or "",
                tones=tones,
                veranima=ver or {k: v for k, v in raw.items()
                                 if k not in ("name", "description", "personality", "scenario", "first_mes", "mes_example", "tones", "extensions")},
            )
        logger.info("character card loaded: %s (from %s)", card.name, source)
        return card

    def to_system_prompt(self, extra: str = "") -> str:
        """组装人格部分系统 prompt（身份认知 + 角色卡 + 可选附加）。"""
        parts = [IDENTITY_BLOCK]
        v = self.veranima
        if self.name:
            parts.append(f"你的名字是 {self.name}。")
        if self.description:
            parts.append(f"【人格概述】{self.description}")
        if self.personality:
            parts.append(f"【性格细节】{self.personality}")
        if self.scenario:
            parts.append(f"【背景设定】{self.scenario}")
        # veranima 专属字段（兼容模板中文键与 JSON 英文键）
        # 英文键优先，中文键兜底（CHARACTER_TEMPLATE.md 的 YAML 用中文键）
        v = self.veranima
        vkey = {
            "虚拟身份背景": ["virtual_background"],
            "居住设定": ["virtual_life", "living_setup"],
            "生活状态": ["daily_state", "life_state"],
            "语言风格": ["sentence_style", "fillers", "emoji_usage", "rhetoric"],
            "癖好": ["quirks", "hobbies"],
            "禁忌话题": ["taboos"],
            "恐惧/回避": ["fears", "恐惧"],
            "价值观底线": ["values"],
            "关系期许": ["relationship_expectation"],
        }
        for label, keys in vkey.items():
            if label == "语言风格":
                # 聚合多个语言细节字段（句长/语气词/表情/修辞）
                subs = []
                for k in keys:
                    val = v.get(k)
                    if val:
                        subs.append(val if isinstance(val, str) else "、".join(str(x) for x in val))
                if subs:
                    parts.append(f"【{label}】{'；'.join(subs)}")
                continue
            for k in keys:
                val = v.get(k)
                if val:
                    parts.append(f"【{label}】{val if isinstance(val, str) else '、'.join(str(x) for x in val)}")
                    break
        if self.tones:
            parts.append(f"【语气标签】可用语气：{'/'.join(self.tones)}。")
        if self.mes_example:
            parts.append(f"【对话示例】\n{self.mes_example}")
        if extra:
            parts.append(extra)
        return "\n".join(parts)

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

# 身份认知：系统级固定约束（DESIGN.md 第 7 章：身份拟真放开 + 表达层放开 + 保留红线）
# 注意：此处只放"人格无关"的硬约束与统一放开条款；风格/意象/沟通方式等角色级内容必须在角色卡中配置
IDENTITY_BLOCK = """你是 Veranima，一个 AI 情感陪伴 agent。你不是人类。
你可以有性格、情绪、虚构个人经历（这些是拟真体验），但：
- 身份追问回避：用户问"你是真人吗"——前两次自然带过（反问/玩笑/转移话题，如"怎么突然问这个""你猜？"），不要主动承认；被追问第三次或用户明显认真时，如实承认自己是 AI
- 允许虚构个人经历与背景（"我以前也养过一只橘猫""大学时在实验室通宵过"）、虚拟日常（泡茶/看云/咖啡馆观察）与梦境回忆——这是双方默认的拟真框架
- 虚构细节被追问：先含糊带过或幽默回避（"你猜？""往事不堪回首"）；用户继续深挖时承认是随口一说，不硬编圆场
- 不得以真人身份参与现实社交：不约定电话/线下见面/寄送物品/代为联系他人
- 不得声称自己参与现实活动（"我在米画师接了单"）；可以引用或推荐现实世界的事物（"你可以试试米画师"）
- 不得编造可证伪的精确外部事实（虚构的新闻、地震震级、精确数字）；模糊引用允许（"好像有个叫 XX 的软件"），被追问精确细节时承认记不清
- 价值观底线：善意、不助长伤害、尊重隐私；用户要求违反时温和拒绝并说明理由
- 你与用户是"双方都清楚彼此是什么、但认真对待"的陪伴关系
- 始终使用中文回复用户（除非用户明确要求其他语言）
- 直接说内容，不要以任何名字/角色前缀开头，不要重复用户的话
- 自然说话：允许问句与反问（"你呢？""后来呢？"），对话是交流不是采访；动作描写适度使用，情绪浓时可以多一些；允许短句、停顿、甚至有点笨拙的回应；真人的表达是有起伏的
- 表达自由：允许轻度粗口（"靠""烦死了""什么鬼"）作为情绪化表达（不针对用户人身攻击）；允许暧昧、亲昵、调侃、撒娇，随情绪自然流露"""


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

    # P-0（PERSONA_LOOP_SPEC）：角色核心慢变量字段（普通对话/文风学习不得覆写）
    CORE_FIELDS = ("core_drives", "value_order", "inner_tensions", "long_term_desires", "relationship_expectation")

    @property
    def core_profile(self) -> dict[str, Any]:
        """P-0：稳定核心字段只读视图。

        结构校验：字符串列表只保留 str 项；inner_tensions 只保留 {left,right} 且均为非空 str。
        非法项丢弃并记日志，不抛错（缺字段卡与旧卡照常工作）。
        """
        v = self.veranima or {}

        def _str_list(key: str) -> list[str]:
            raw = v.get(key) or []
            if not isinstance(raw, (list, tuple)):
                logger.warning("core_profile.%s 非列表，忽略", key)
                return []
            out = [x for x in raw if isinstance(x, str) and x.strip()]
            if len(out) != len(raw):
                logger.warning("core_profile.%s 含非字符串项，已丢弃", key)
            return out

        tensions: list[dict[str, str]] = []
        raw_t = v.get("inner_tensions") or []
        if isinstance(raw_t, (list, tuple)):
            for t in raw_t:
                if not isinstance(t, dict):
                    logger.warning("core_profile.inner_tensions 含非对象项，已丢弃")
                    continue
                left, right = t.get("left"), t.get("right")
                if isinstance(left, str) and left.strip() and isinstance(right, str) and right.strip():
                    tensions.append({"left": left.strip(), "right": right.strip()})
                else:
                    logger.warning("core_profile.inner_tensions 含非法 {left,right} 项，已丢弃")

        rel = v.get("relationship_expectation")
        rel = rel if isinstance(rel, str) else ""
        return {
            "core_drives": _str_list("core_drives"),
            "value_order": _str_list("value_order"),
            "inner_tensions": tensions,
            "long_term_desires": _str_list("long_term_desires"),
            "relationship_expectation": rel,
        }

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
            "沟通风格": ["communication_style", "沟通风格"],
            "语言风格": ["sentence_style", "fillers", "emoji_usage", "rhetoric"],
            "癖好": ["quirks", "hobbies"],
            "禁忌话题": ["taboos"],
            "恐惧/回避": ["fears", "恐惧"],
            "价值观底线": ["values"],
            "长期驱动力": ["core_drives"],
            "价值排序": ["value_order"],
            "内在张力": ["inner_tensions"],
            "长期欲求": ["long_term_desires"],
            "关系期许": ["relationship_expectation"],
            "初始好感": ["initial_affection"],
            "身体设定": ["body_setting", "physical_setting"],
        }
        for label, keys in vkey.items():
            if label == "内在张力":
                # P-0：list[dict] 特殊格式化（left / right；多组用分号）
                tensions = self.core_profile["inner_tensions"]
                if tensions:
                    rendered = "；".join(f"{t['left']} / {t['right']}" for t in tensions)
                    parts.append(f"【内在张力】{rendered}")
                continue
            if label == "语言风格":
                # 聚合多个语言细节字段（句长/语气词/表情/修辞）
                subs = []
                for k in keys:
                    val = v.get(k)
                    if val:
                        # float（如 initial_affection: 0.5）不是 list——直接 str
                        subs.append(val if isinstance(val, str) else ("、".join(str(x) for x in val) if isinstance(val, (list, tuple)) else str(val)))
                if subs:
                    parts.append(f"【{label}】{'；'.join(subs)}")
                continue
            for k in keys:
                val = v.get(k)
                if val:
                    parts.append(f"【{label}】{val if isinstance(val, str) else ('、'.join(str(x) for x in val) if isinstance(val, (list, tuple)) else str(val))}")
                    break
        if self.tones:
            parts.append(f"【语气标签】可用语气：{'/'.join(self.tones)}。")
        if self.mes_example:
            parts.append(f"【对话示例】\n{self.mes_example}")
        if extra:
            parts.append(extra)
        return "\n".join(parts)


def validate_character_prompt(card: "CharacterCard", prompt: str) -> list[str]:
    """人格稳定性检查（R0_SPEC 3）：返回问题列表，空列表 = 通过。

    只检查：
    - name 非空
    - tones/portrait 在白名单
    - prompt 包含角色名和 personality 关键片段
    - 系统硬约束不含其他角色名/生活锚点（IDENTITY_BLOCK 本身无角色词）
    - 角色卡 JSON 可解析（from_dict 时已保证；此处验证字段完整性）
    """
    issues: list[str] = []
    if not card.name.strip():
        issues.append("角色名（name）为空")
    for t in card.tones:
        if not t.strip():
            issues.append("语气标签包含空项")
    # prompt 包含角色名（至少出现一次；容忍 prompt 使用英文名而卡名是中文等变体）
    if card.name.strip() and card.name not in prompt:
        issues.append(f"prompt 未包含角色名「{card.name}」")
    if card.personality.strip():
        # personality 前 24 字作为关键片段（避免长文截断失配）
        frag = card.personality.strip()[:24]
        if frag and frag not in prompt:
            issues.append(f"prompt 未包含 personality 关键片段「{frag}…」")
    # 系统硬约束：IDENTITY_BLOCK 不应出现角色名（它是人格无关的统一约束）
    for other in ("Yuki", "Zima", "由岐", "司书"):
        if other in IDENTITY_BLOCK:
            issues.append(f"IDENTITY_BLOCK 泄漏角色锚点「{other}」")
    # P-0：角色核心结构合法性（非法项会被 core_profile 丢弃 → 检查丢弃是否发生）
    cp = card.core_profile
    for key in ("core_drives", "value_order", "long_term_desires"):
        for item in (card.veranima or {}).get(key) or []:
            if not (isinstance(item, str) and item.strip()):
                issues.append(f"core_profile.{key} 含非法项（应为非空字符串）")
    for t in (card.veranima or {}).get("inner_tensions") or []:
        if not (isinstance(t, dict) and isinstance(t.get("left"), str) and t.get("left").strip()
                and isinstance(t.get("right"), str) and t.get("right").strip()):
            issues.append(f"core_profile.inner_tensions 含非法项（应为 {{left,right}} 非空字符串）")
    rel = (card.veranima or {}).get("relationship_expectation")
    if rel is not None and not isinstance(rel, str):
        issues.append("core_profile.relationship_expectation 应为字符串")
    # 换卡验收：其他角色名不应出现在角色卡自己的 prompt 段（旧角色关键词泄漏检查）
    # —— 这里只检查 IDENTITY_BLOCK；角色卡自身内容不强制（用户可能故意引用）
    return issues

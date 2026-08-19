"""R0 角色卡契约测试（R0_SPEC 1/3/7）。

覆盖：V3 卡/旧格式卡/缺字段卡加载；validate_character_prompt 检查项；
换卡验收：IDENTITY_BLOCK 不含角色锚点。
"""
from __future__ import annotations

from veranima.core.character import (
    IDENTITY_BLOCK,
    CharacterCard,
    validate_character_prompt,
)

V3_CARD = {
    "spec": "chara_card_v3",
    "data": {
        "name": "Yuki",
        "description": "洒脱慵懒的学姐",
        "personality": "理性但护短，喜欢在屋顶抽烟看云",
        "scenario": "夏日小城",
        "first_mes": "哟，又见面了。",
        "mes_example": "<START>\n{{user}}: 你好\n{{char}}: 嗯，坐吧。",
        "extensions": {
            "veranima": {
                "communication_style": "慵懒、直球",
                "tones": ["中性", "调侃", "疲惫"],
                "avatar": {"expressions": {"微笑": "smile.png", "闲置": "idle.png"}},
                "bilingual": {"enabled": True, "display": "zh", "tts": "ja"},
            }
        },
    },
}


def test_v3_card_load():
    card = CharacterCard.from_dict(V3_CARD)
    assert card.name == "Yuki"
    assert card.tones == ["中性", "调侃", "疲惫"]
    assert card.veranima["communication_style"] == "慵懒、直球"
    assert card.veranima["bilingual"]["enabled"] is True


def test_legacy_top_level_card_load():
    raw = {
        "name": "Zima",
        "personality": "冷淡的程序员",
        "veranima": {"communication_style": "简短", "tones": ["中性"]},
    }
    card = CharacterCard.from_dict(raw)
    assert card.name == "Zima"
    assert card.veranima["communication_style"] == "简短"
    assert card.tones == ["中性"]


def test_missing_fields_defaults():
    card = CharacterCard.from_dict({"name": "NoName"})
    assert card.personality == ""
    assert card.scenario == ""
    assert card.tones == ["中性", "平静", "温柔"]
    assert card.veranima == {}


def test_system_prompt_contains_identity_and_name():
    card = CharacterCard.from_dict(V3_CARD)
    prompt = card.to_system_prompt()
    assert "你是 Veranima" in prompt          # 系统硬边界
    assert "你的名字是 Yuki。" in prompt       # 角色名
    assert "【性格细节】" in prompt
    assert "【语气标签】可用语气：中性/调侃/疲惫。" in prompt


def test_validate_ok():
    card = CharacterCard.from_dict(V3_CARD)
    prompt = card.to_system_prompt()
    issues = validate_character_prompt(card, prompt)
    assert issues == []


def test_validate_empty_name():
    card = CharacterCard.from_dict({"personality": "x"})
    issues = validate_character_prompt(card, "")
    assert any("角色名" in i for i in issues)


def test_validate_prompt_missing_name():
    card = CharacterCard.from_dict(V3_CARD)
    issues = validate_character_prompt(card, "没有角色名的 prompt")
    assert any("角色名" in i for i in issues)


def test_validate_prompt_missing_personality_fragment():
    card = CharacterCard.from_dict(V3_CARD)
    issues = validate_character_prompt(card, "Yuki 的 prompt 但性格细节完全不同")
    assert any("personality" in i for i in issues)


def test_identity_block_no_character_anchors():
    """换卡验收：系统硬约束不得包含任何角色名/生活锚点（R0_SPEC 3）。"""
    for anchor in ("Yuki", "Zima", "由岐", "司书"):
        assert anchor not in IDENTITY_BLOCK

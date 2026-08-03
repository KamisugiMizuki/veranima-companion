"""角色卡机制测试：系统级约束与角色级内容分离（换卡不串味）。"""

from __future__ import annotations

from veranima.core.character import CharacterCard, IDENTITY_BLOCK

# 系统级约束中不应出现角色级内容（换角色卡时这些会串味）
ROLE_LEVEL_KEYWORDS = ["绿萝", "面包店", "梧桐", "咖啡杯", "锚点", "先回应情绪", "鲸鱼", "落叶"]


def test_identity_block_has_no_role_content():
    """IDENTITY_BLOCK 只含人格无关硬约束。"""
    for kw in ROLE_LEVEL_KEYWORDS:
        assert kw not in IDENTITY_BLOCK, f"角色级内容泄漏到系统级: {kw}"


def test_identity_block_keeps_hard_constraints():
    """系统级硬约束必须保留。"""
    for kw in ["不得声称自己是真实人类", "不要以问句结尾", "不得编造\"不实\"的具体外部事实",
               "温和拒绝并说明理由"]:
        assert kw in IDENTITY_BLOCK


def test_rational_card_not_polluted():
    """理性卡（无 communication_style）不应被小V的角色内容污染。"""
    rational = CharacterCard(
        name="Dr. Logic",
        description="极端理性的分析型 AI",
        personality="冷静、直接、只讲事实与逻辑",
    )
    sp = rational.to_system_prompt()
    for kw in ["绿萝", "面包店", "梧桐", "锚点", "先回应情绪"]:
        assert kw not in sp
    # 通用约束仍在
    assert "不要以问句结尾" in sp


def test_communication_style_injected():
    """小V 卡的 communication_style 注入 prompt。"""
    card = CharacterCard.from_file("config/character.json")
    sp = card.to_system_prompt()
    assert "【沟通风格】" in sp
    assert "绿萝" in sp

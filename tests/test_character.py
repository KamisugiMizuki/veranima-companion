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
    """系统级硬约束必须保留（2026-08 改写：身份拟真放开后仍保留红线）。"""
    for kw in ["身份追问回避", "不得以真人身份参与现实社交", "不得声称自己参与现实活动",
               "不助长伤害", "温和拒绝并说明理由"]:
        assert kw in IDENTITY_BLOCK


def test_identity_block_allows_expression_freedom():
    """表达层放开条款已写入系统级（2026-08）。"""
    for kw in ["允许问句与反问", "表达自由", "暧昧", "虚构个人经历"]:
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
    assert "不得以真人身份参与现实社交" in sp


def test_initial_affection_injected():
    """initial_affection 字段注入 prompt（【初始好感】块）。"""
    card = CharacterCard(
        name="测试卡",
        veranima={"initial_affection": "对用户怀有初始好感与暧昧底色"},
    )
    sp = card.to_system_prompt()
    assert "【初始好感】" in sp
    assert "初始好感与暧昧底色" in sp


def test_communication_style_injected():
    """communication_style 字段注入 prompt（机制验证，不依赖运行时卡内容）。"""
    card = CharacterCard(
        name="测试卡",
        veranima={"communication_style": "先回应情绪，再展开内容；固定生活锚点（窗台的绿萝）"},
    )
    sp = card.to_system_prompt()
    assert "【沟通风格】" in sp
    assert "绿萝" in sp

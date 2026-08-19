"""P-0 角色核心扩展（PERSONA_LOOP_SPEC P-0）：角色卡核心字段读取/注入/验证。"""
from __future__ import annotations

import pytest

from veranima.core.character import CharacterCard, validate_character_prompt


def _card(veranima: dict) -> CharacterCard:
    return CharacterCard(name="测试卡", personality="理性但护短", veranima=veranima)


def _full_veranima() -> dict:
    return {
        "core_drives": ["想理解用户，但不愿无条件顺从"],
        "value_order": ["关系诚实", "独立判断", "保护重要的人"],
        "inner_tensions": [{"left": "渴望靠近", "right": "害怕失去边界"}],
        "long_term_desires": ["成为能共同生活和创作的长期伙伴"],
        "relationship_expectation": "亲密但保留彼此独立性",
    }


def test_core_profile_reads_fields():
    card = _card(_full_veranima())
    cp = card.core_profile
    assert cp["core_drives"] == ["想理解用户，但不愿无条件顺从"]
    assert cp["value_order"] == ["关系诚实", "独立判断", "保护重要的人"]
    assert cp["inner_tensions"] == [{"left": "渴望靠近", "right": "害怕失去边界"}]
    assert cp["long_term_desires"] == ["成为能共同生活和创作的长期伙伴"]
    assert cp["relationship_expectation"] == "亲密但保留彼此独立性"


def test_core_profile_defaults_when_missing():
    card = _card({})
    cp = card.core_profile
    assert cp["core_drives"] == []
    assert cp["value_order"] == []
    assert cp["inner_tensions"] == []
    assert cp["long_term_desires"] == []
    assert cp["relationship_expectation"] == ""


def test_inner_tensions_invalid_entries_dropped():
    """inner_tensions 必须是 [{left,right}]，缺字段/非字符串/非 dict 拒绝。"""
    card = _card({
        "inner_tensions": [
            {"left": "渴望靠近", "right": "害怕失去边界"},   # 合法
            {"left": "只有左"},                            # 缺 right
            {"not_a_tension": True},                       # 缺 left/right
            "字符串不是张力",                               # 非 dict
            {"left": 123, "right": "数字不是字符串"},        # 非字符串
        ]
    })
    assert card.core_profile["inner_tensions"] == [{"left": "渴望靠近", "right": "害怕失去边界"}]


def test_inner_tensions_non_string_items_dropped():
    card = _card({
        "core_drives": ["有效", 42, None],
        "value_order": ["有效", {"bad": 1}],
        "long_term_desires": [True, "有效"],
    })
    cp = card.core_profile
    assert cp["core_drives"] == ["有效"]
    assert cp["value_order"] == ["有效"]
    assert cp["long_term_desires"] == ["有效"]


def test_prompt_injects_core_profile_once_each():
    card = _card(_full_veranima())
    prompt = card.to_system_prompt()
    assert "【长期驱动力】想理解用户，但不愿无条件顺从" in prompt
    assert "【价值排序】关系诚实、独立判断、保护重要的人" in prompt
    assert "【内在张力】渴望靠近 / 害怕失去边界" in prompt
    assert "【长期欲求】成为能共同生活和创作的长期伙伴" in prompt
    assert "【关系期许】亲密但保留彼此独立性" in prompt
    # 每个标签只出现一次
    for label in ("长期驱动力", "价值排序", "内在张力", "长期欲求", "关系期许"):
        assert prompt.count(f"【{label}】") == 1


def test_prompt_no_core_profile_when_empty():
    prompt = _card({}).to_system_prompt()
    assert "【长期驱动力】" not in prompt
    assert "【价值排序】" not in prompt
    assert "【内在张力】" not in prompt
    assert "【长期欲求】" not in prompt


def test_identity_block_has_no_character_words():
    from veranima.core.character import IDENTITY_BLOCK
    for anchor in ("Yuki", "Zima", "由岐", "司书", "绿萝", "长期驱动力"):
        assert anchor not in IDENTITY_BLOCK


def test_validate_character_prompt_core_structure():
    """结构合法 → 无 issue；非法 inner_tensions → issue。"""
    ok = _card(_full_veranima())
    assert validate_character_prompt(ok, ok.to_system_prompt()) == []

    bad = _card({"inner_tensions": [{"left": "只有左"}]})
    issues = validate_character_prompt(bad, bad.to_system_prompt())
    assert any("inner_tensions" in i for i in issues)


def test_from_file_loads_core_profile(tmp_path):
    import json
    p = tmp_path / "card.json"
    p.write_text(json.dumps({
        "spec": "chara_card_v3",
        "data": {
            "name": "Yuki",
            "personality": "洒脱",
            "extensions": {"veranima": _full_veranima()},
        },
    }, ensure_ascii=False), encoding="utf-8")
    card = CharacterCard.from_file(p)
    assert card.name == "Yuki"
    assert card.core_profile["core_drives"] == ["想理解用户，但不愿无条件顺从"]

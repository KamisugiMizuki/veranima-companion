"""Yuki 运行时角色卡的真实加载与资源契约。"""
from __future__ import annotations

from pathlib import Path

from veranima.core.character import CharacterCard, validate_character_prompt


ROOT = Path(__file__).resolve().parents[1]
CARD_PATH = ROOT / "characters" / "yuki" / "character.json"


def test_yuki_card_is_v3_loadable_and_prompt_valid():
    card = CharacterCard.from_file(CARD_PATH)
    prompt = card.to_system_prompt()

    assert card.name == "水上由岐"
    assert card.veranima["initial_affection"] == 0.5
    assert len(card.core_profile["core_drives"]) >= 3
    assert "明快" in prompt or "率直" in prompt
    assert "原作台词" in prompt
    assert validate_character_prompt(card, prompt) == []


def test_yuki_avatar_paths_are_project_local():
    expressions = CharacterCard.from_file(CARD_PATH).veranima["avatar"]["expressions"]
    assert len(expressions) >= 10
    for relative in expressions.values():
        path = CARD_PATH.parent / relative
        assert path.exists(), relative
        assert path.resolve().is_relative_to(CARD_PATH.parent.resolve())


def test_yuki_source_boundary_is_explicit():
    source = CharacterCard.from_file(CARD_PATH).veranima["source_basis"]
    assert source["status"] == "public-profile-adaptation"
    assert source["reviewed_sources"]
    assert "原作台词、段落、场景文本和逐句口吻复制" in source["excluded"]
    assert source["project_original_extensions"]
"""当前角色包契约：V3、P-0 核心、目录资源和 Electron 可读取的表情映射。"""
from __future__ import annotations

import json
from pathlib import Path

from veranima.core.character import CharacterCard, validate_character_prompt
from veranima.core.roles import list_roles

ROOT = Path(__file__).resolve().parents[1]


def test_all_role_cards_are_v3_and_loadable():
    roles = list_roles()
    assert {r["id"] for r in roles} >= {"zima", "yuki"}
    for role in roles:
        raw = json.loads(Path(role["path"]).read_text(encoding="utf-8"))
        assert raw["spec"] == "chara_card_v3"
        card = CharacterCard.from_file(role["path"])
        assert card.name == role["name"]
        assert card.core_profile["core_drives"]
        assert card.core_profile["value_order"]
        assert card.core_profile["inner_tensions"]
        assert card.core_profile["long_term_desires"]
        assert card.veranima.get("avatar", {}).get("expressions")


def test_role_portraits_resolve_from_card_directory():
    for role_id in ("zima", "yuki"):
        path = ROOT / "characters" / role_id / "character.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        expressions = raw["data"]["extensions"]["veranima"]["avatar"]["expressions"]
        assert expressions
        for rel in expressions.values():
            assert (path.parent / rel).exists(), (role_id, rel)


def test_role_prompt_has_core_and_no_other_role_name():
    for role_id, other in (("zima", "水上由岐"), ("yuki", "Зима")):
        card = CharacterCard.from_file(ROOT / "characters" / role_id / "character.json")
        prompt = card.to_system_prompt()
        assert "【长期驱动力】" in prompt
        assert "【内在张力】" in prompt
        assert other not in prompt
        assert validate_character_prompt(card, prompt) == []

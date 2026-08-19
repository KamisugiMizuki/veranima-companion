"""角色包 V3 导入回写契约：立绘说明和重名显示名不能写到 V3 根节点。"""
from __future__ import annotations

import json
from pathlib import Path

from veranima.core.character_archive import apply_portrait_description


def test_apply_portrait_description_writes_inside_v3_extensions(tmp_path):
    portraits = tmp_path / "portraits"
    portraits.mkdir()
    (portraits / "idle_a.png").write_bytes(b"png")
    (portraits / "image_description.txt").write_text("idle_ 闲置\n", encoding="utf-8")
    card = {
        "spec": "chara_card_v3",
        "data": {"name": "测试", "extensions": {"veranima": {}}},
    }
    path = tmp_path / "character.json"
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    assert apply_portrait_description(tmp_path) == {"闲置": "portraits/idle_a.png"}
    out = json.loads(path.read_text(encoding="utf-8"))
    assert out["data"]["extensions"]["veranima"]["avatar"]["expressions"]["闲置"] == "portraits/idle_a.png"
    assert "extensions" not in out

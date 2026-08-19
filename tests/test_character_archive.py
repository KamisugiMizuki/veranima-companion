"""DESIGN 4.8 现实行动边界 + 4.11 角色包导入导出测试。"""
import json
import zipfile

import pytest

from veranima.core.character_archive import (
    MAX_ARCHIVE_COMPRESSION_RATIO,
    CharacterArchiveError,
    apply_portrait_description,
    export_character,
    import_character,
)
from veranima.core.prompts import REALITY_BOUNDARY, build_system_prompt


@pytest.fixture
def agent(tmp_path):
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    from veranima.memory.store import MemoryStore
    mem = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, llm_config={})
    card = CharacterCard(name="测试", first_mes="hi")
    return card, mem


# ---------- 现实行动边界 prompt 块 ----------

def test_reality_boundary_in_prompt(agent):
    """system prompt 包含现实行动边界块（不暴露身份的表达）。"""
    card, memory = agent
    sysp = build_system_prompt(card, __import__("veranima.core.state", fromlist=["AgentState"]).AgentState(), memory)
    assert "现实行动边界" in sysp
    assert "像真人一样自然地婉拒" in sysp
    assert "不虚构自己参与过现实活动" in sysp
    # 不应暴露身份（拟真路线：不声明"你是桌宠/AI"）
    assert "你是桌宠" not in sysp
    assert "我是AI" not in sysp


# ---------- 角色包导出/导入 ----------

def _make_character_dir(tmp_path):
    d = tmp_path / "characters" / "小V"
    d.mkdir(parents=True)
    (d / "character.json").write_text(json.dumps({
        "id": "xiaov", "display_name": "小V", "initial_message": "你好",
        "card": "card.md",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "card.md").write_text("小V的人格描述", encoding="utf-8")
    (d / "portraits").mkdir()
    (d / "portraits" / "idle.png").write_bytes(b"\x89PNG fake")
    return d


def test_export_import_roundtrip(tmp_path):
    """导出 → 导入：character.json/card.md/portraits 完整保留。"""
    src = _make_character_dir(tmp_path)
    archive = tmp_path / "out" / "xiaov.char"
    export_character(src, archive)
    assert archive.exists()

    chars = tmp_path / "imported"
    target = import_character(archive, chars)
    assert target.name == "xiaov"
    assert (target / "character.json").exists()
    assert (target / "card.md").read_text(encoding="utf-8") == "小V的人格描述"
    assert (target / "portraits" / "idle.png").read_bytes() == b"\x89PNG fake"


def test_import_duplicate_renames(tmp_path):
    """重复导入：自动改名（id-2），不覆盖已有角色。"""
    src = _make_character_dir(tmp_path)
    archive = tmp_path / "xiaov.char"
    export_character(src, archive)
    chars = tmp_path / "chars"
    first = import_character(archive, chars)
    second = import_character(archive, chars)
    assert first.name == "xiaov"
    assert second.name == "xiaov-2"
    assert (first / "character.json").exists()
    assert (second / "character.json").exists()


def test_import_rejects_path_traversal(tmp_path):
    """路径穿越（../evil）被拒。"""
    bad = tmp_path / "bad.char"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"id": "evil"}))
        zf.writestr("character/../evil.txt", "x")
    with pytest.raises(CharacterArchiveError, match="非法路径"):
        import_character(bad, tmp_path / "chars")


def test_import_rejects_bad_id(tmp_path):
    """非法 id（含中文/空格）被拒。"""
    bad = tmp_path / "bad2.char"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"id": "小V bad!"}))
    with pytest.raises(CharacterArchiveError, match="非法 id"):
        import_character(bad, tmp_path / "chars")


def test_import_rejects_missing_manifest(tmp_path):
    """缺 manifest.json 被拒。"""
    bad = tmp_path / "bad3.char"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("character/card.md", "x")
    with pytest.raises(CharacterArchiveError, match="manifest"):
        import_character(bad, tmp_path / "chars")


def test_import_rejects_zip_bomb(tmp_path):
    """超高压缩比（zip bomb 特征）被拒。"""
    bomb = tmp_path / "bomb.char"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"id": "bomb"}))
        zf.writestr("character/card.md", "A" * (MAX_ARCHIVE_COMPRESSION_RATIO * 200))  # 超高压缩比
    with pytest.raises(CharacterArchiveError, match="压缩比"):
        import_character(bomb, tmp_path / "chars")


def test_apply_portrait_description(tmp_path):
    """立绘说明.txt 前缀批量映射 → avatar.expressions（R4_SPEC 2.3）。"""
    d = _make_character_dir(tmp_path)
    # 覆盖立绘说明
    portraits = d / "portraits"
    (portraits / "happy.png").write_bytes(b"\x89PNG happy")
    (portraits / "sad.png").write_bytes(b"\x89PNG sad")
    (portraits / "立绘说明.txt").write_text("happy 开心脸红\nsad 难过\n", encoding="utf-8")

    mapping = apply_portrait_description(d)
    assert mapping == {"开心脸红": "portraits/happy.png", "难过": "portraits/sad.png"}
    # 写回 character.json
    data = json.loads((d / "character.json").read_text(encoding="utf-8"))
    exprs = data["extensions"]["veranima"]["avatar"]["expressions"]
    assert exprs["开心脸红"] == "portraits/happy.png"


def test_import_applies_portrait_description(tmp_path):
    """导入角色包时自动应用立绘说明.txt。"""
    d = _make_character_dir(tmp_path)
    (d / "portraits" / "happy.png").write_bytes(b"\x89PNG happy")
    (d / "portraits" / "立绘说明.txt").write_text("happy 开心脸红\n", encoding="utf-8")
    archive = tmp_path / "xiaov.char"
    export_character(d, archive)

    chars = tmp_path / "chars"
    imported = import_character(archive, chars)
    data = json.loads((imported / "character.json").read_text(encoding="utf-8"))
    exprs = data["extensions"]["veranima"]["avatar"]["expressions"]
    assert exprs["开心脸红"] == "portraits/happy.png"

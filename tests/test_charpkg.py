""".charpkg 最小安全包契约。"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from veranima.core.character_archive import CharacterArchiveError, export_character, import_character


def _character_dir(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "portraits").mkdir(parents=True)
    (root / "character.json").write_text(json.dumps({
        "spec": "chara_card_v3",
        "data": {"name": "测试角色", "extensions": {"veranima": {
            "avatar": {"expressions": {"闲置": "portraits/idle.png"}},
        }}},
    }, ensure_ascii=False), encoding="utf-8")
    (root / "card.md").write_text("# 测试角色\n", encoding="utf-8")
    (root / "portraits" / "idle.png").write_bytes(b"not-a-real-image")
    (root / "voice" / "models").mkdir(parents=True)
    (root / "voice" / "models" / "unsafe.ckpt").write_bytes(b"pickle-like")
    (root / "voice" / "refs").mkdir(parents=True)
    (root / "voice" / "refs" / "private.wav").write_bytes(b"voice")
    (root / "example_voices").mkdir()
    (root / "example_voices" / "private.ogg").write_bytes(b"example voice")
    return root


def test_charpkg_roundtrip_has_manifest_and_checksums(tmp_path: Path):
    source = _character_dir(tmp_path)
    package = tmp_path / "测试角色.charpkg"

    export_character(source, package)

    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        checksums = json.loads(archive.read("checksums.json"))
    assert manifest["package_format"] == "veranima.charpkg"
    assert manifest["schema_version"] == 1
    assert manifest["display_name"] == "测试角色"
    assert manifest["character_id"] == "source"
    assert manifest["entrypoints"]["character"] == "character/character.json"
    assert checksums["character/character.json"] == hashlib.sha256(
        (source / "character.json").read_bytes()
    ).hexdigest()
    assert "character/voice/models/unsafe.ckpt" not in checksums
    assert "character/voice/refs/private.wav" not in checksums
    assert "character/example_voices/private.ogg" not in checksums

    installed = import_character(package, tmp_path / "characters")
    assert (installed / "character.json").exists()
    assert (installed / "portraits" / "idle.png").read_bytes() == b"not-a-real-image"


def test_charpkg_asset_options_no_portraits_and_with_voice(tmp_path: Path):
    """安卓轻量包（无立绘）与 PC 搬运包（含 refs 不含 models）两个开关。"""
    source = _character_dir(tmp_path)

    lite = tmp_path / "lite.charpkg"
    export_character(source, lite, include_portraits=False)
    with zipfile.ZipFile(lite) as archive:
        names = set(archive.namelist())
    assert not any(n.startswith("character/portraits/") for n in names)
    installed = import_character(lite, tmp_path / "characters")  # 表达式引用缺文件不拦截
    assert not (installed / "portraits").exists()

    full = tmp_path / "with-voice.charpkg"
    export_character(source, full, include_voice=True)
    with zipfile.ZipFile(full) as archive:
        checksums = json.loads(archive.read("checksums.json"))
    assert "character/voice/refs/private.wav" in checksums
    assert "character/example_voices/private.ogg" in checksums
    assert "character/voice/models/unsafe.ckpt" not in checksums  # 权重永不入包


def test_charpkg_rejects_tampered_member(tmp_path: Path):
    source = _character_dir(tmp_path)
    package = tmp_path / "test.charpkg"
    export_character(source, package)
    tampered = tmp_path / "tampered.charpkg"
    with zipfile.ZipFile(package) as original, zipfile.ZipFile(tampered, "w") as out:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "character/card.md":
                content = b"changed after manifest"
            out.writestr(info.filename, content)

    with pytest.raises(CharacterArchiveError, match="校验和"):
        import_character(tampered, tmp_path / "characters")


def test_charpkg_rejects_manifest_file_list_mismatch(tmp_path: Path):
    source = _character_dir(tmp_path)
    package = tmp_path / "valid.charpkg"
    export_character(source, package)
    broken = tmp_path / "broken-manifest.charpkg"
    with zipfile.ZipFile(package) as original, zipfile.ZipFile(broken, "w") as out:
        manifest = json.loads(original.read("manifest.json"))
        manifest["files"] = []
        for info in original.infolist():
            content = json.dumps(manifest).encode() if info.filename == "manifest.json" else original.read(info.filename)
            out.writestr(info.filename, content)
    with pytest.raises(CharacterArchiveError, match="manifest files"):
        import_character(broken, tmp_path / "characters")


def test_legacy_char_still_imports(tmp_path: Path):
    source = _character_dir(tmp_path)
    archive = tmp_path / "legacy.char"
    export_character(source, archive, package_format="legacy")

    installed = import_character(archive, tmp_path / "characters")
    assert (installed / "character.json").exists()


def test_charpkg_rejects_windows_path_traversal(tmp_path: Path):
    bad = tmp_path / "bad.charpkg"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "package_format": "veranima.charpkg", "schema_version": 1,
            "character_id": "bad", "display_name": "bad",
        }))
        archive.writestr("checksums.json", "{}")
        archive.writestr("character\\..\\evil.txt", "x")
    with pytest.raises(CharacterArchiveError, match="非法路径"):
        import_character(bad, tmp_path / "characters")


def test_charpkg_rejects_normalized_duplicate_paths(tmp_path: Path):
    bad = tmp_path / "duplicate.charpkg"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("character/card.md", "a")
            archive.writestr("character\\card.md", "b")
    with pytest.raises(CharacterArchiveError, match="重复路径"):
        import_character(bad, tmp_path / "characters")


def test_charpkg_invalid_character_json_leaves_no_install(tmp_path: Path):
    source = _character_dir(tmp_path)
    package = tmp_path / "valid.charpkg"
    export_character(source, package)
    broken = tmp_path / "broken.charpkg"
    with zipfile.ZipFile(package) as original, zipfile.ZipFile(broken, "w") as out:
        checksums = json.loads(original.read("checksums.json"))
        manifest = json.loads(original.read("manifest.json"))
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "character/character.json":
                content = b"{broken"
                checksums[info.filename] = hashlib.sha256(content).hexdigest()
                for item in manifest["files"]:
                    if item["path"] == info.filename:
                        item["sha256"] = checksums[info.filename]
                        item["bytes"] = len(content)
            if info.filename in {"checksums.json", "manifest.json"}:
                continue
            out.writestr(info.filename, content)
        out.writestr("checksums.json", json.dumps(checksums))
        out.writestr("manifest.json", json.dumps(manifest))

    characters = tmp_path / "characters"
    with pytest.raises(CharacterArchiveError, match="character.json"):
        import_character(broken, characters)
    assert not (characters / "source").exists()


def test_charpkg_duplicate_import_renames_v3_display_name(tmp_path: Path):
    source = _character_dir(tmp_path)
    package = tmp_path / "role.charpkg"
    export_character(source, package)
    characters = tmp_path / "characters"
    first = import_character(package, characters)
    second = import_character(package, characters)
    assert first.name == "source"
    assert second.name == "source-2"
    second_card = json.loads((second / "character.json").read_text(encoding="utf-8"))
    assert second_card["data"]["display_name"] == "测试角色(2)"

"""角色包导入导出（DESIGN 4.11，sakura character_archive 借鉴-简化版）。

打包格式：zip 含 manifest.json + character/ 根目录。
- manifest.json: {id, display_name, initial_message, card, portrait, theme, voice...}
- character/: card.md + character.json + portraits/ + voice/（原样保留）

导入安全检查（防恶意包）：
- zip 成员数上限 / 单文件大小上限 / 总大小上限 / 压缩比上限（防 zip bomb）
- 解压路径穿越防护（拒绝 ../ 与绝对路径）
- id 合法性（[A-Za-z0-9_.-]）；重名自动改 id/display_name（防覆盖）
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

# 安全限制（sakura archive_security 同款思路，数值简化）
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024   # 2GB
MAX_ARCHIVE_TOTAL_BYTES = 8 * 1024 * 1024 * 1024    # 8GB
MAX_ARCHIVE_COMPRESSION_RATIO = 200                 # 防 zip bomb

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ARCHIVE_CHARACTER_ROOT = PurePosixPath("character")


class CharacterArchiveError(RuntimeError):
    """角色包格式错误或导入导出失败。"""


# ---------- 导出 ----------

def export_character(dir_path: Path, out_path: Path) -> Path:
    """角色目录（character.json + card.md + portraits/ + voice/）→ .char zip。

    manifest.json 自动生成：id 从角色名派生（slug 化，冲突时加哈希后缀），
    display_name 取角色名。不要求角色卡自带 id 字段（与 sakura 格式兼容）。
    """
    dir_path = Path(dir_path)
    manifest_path = dir_path / "character.json"
    if not manifest_path.exists():
        raise CharacterArchiveError(f"角色目录缺少 character.json: {dir_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    name = str(data.get("name") or data.get("display_name") or "character").strip()
    slug = re.sub(r"[^A-Za-z0-9_.-]", "", name) or ""
    char_id = slug
    # 与 sakura 的 id 字段兼容：角色卡已有 id 则优先
    if str(data.get("id") or "").strip():
        char_id = str(data["id"]).strip()
    if not _SAFE_ID_RE.match(char_id):
        # 非 ASCII 名（如俄文）→ slug 为空/非法 → char-<hash4> 保证可识别且唯一
        char_id = f"char-{hashlib.md5(name.encode()).hexdigest()[:4]}"
    manifest = {
        "id": char_id,
        "display_name": name,
        **{k: v for k, v in data.items() if k not in ("id", "display_name") and not isinstance(v, (dict, list))},
        "card": "card.md" if (dir_path / "card.md").exists() else None,
    }
    manifest = {k: v for k, v in manifest.items() if v is not None}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # manifest.json 放根（sakura 同款）
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # character/ 根目录：其余文件原样保留
        for p in sorted(dir_path.rglob("*")):
            if p.is_file():
                rel = PurePosixPath(ARCHIVE_CHARACTER_ROOT) / p.relative_to(dir_path).as_posix()
                zf.write(p, rel.as_posix())
    return out_path


# ---------- 导入 ----------

def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise CharacterArchiveError(f"角色包成员数超限（>{MAX_ARCHIVE_MEMBERS}）")
    total = 0
    for info in infos:
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise CharacterArchiveError(f"成员过大: {info.filename}")
        total += info.file_size
        # 路径穿越防护
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts:
            raise CharacterArchiveError(f"非法路径: {info.filename}")
        # 压缩比防护（zip bomb）：file_size / compress_size 超限
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO:
            raise CharacterArchiveError(f"压缩比异常（疑似 zip bomb）: {info.filename}")
    if total > MAX_ARCHIVE_TOTAL_BYTES:
        raise CharacterArchiveError("角色包总大小超限")


def apply_portrait_description(char_dir: Path) -> dict[str, str]:
    """应用立绘说明.txt（M4_SPEC 2.3）：按前缀批量绑定表情标签 → avatar.expressions。

    立绘说明.txt 每行「文件前缀 标签」（空格分隔）；匹配：portraits/ 下文件名以
    前缀开头 → 绑定标签。写回 character.json 的 avatar.expressions。返回映射。
    """
    char_dir = Path(char_dir)
    desc_path = char_dir / "portraits" / "立绘说明.txt"
    if not desc_path.exists():
        desc_path = char_dir / "portraits" / "image_description.txt"  # 别名（等价文件）
    if not desc_path.exists():
        return {}
    mapping: dict[str, str] = {}
    for line in desc_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        prefix, label = parts[0].strip(), parts[1].strip()
        for p in (char_dir / "portraits").iterdir():
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} \
                    and p.name.startswith(prefix):
                mapping[label] = f"portraits/{p.name}"
    if not mapping:
        return {}
    # 写回 character.json avatar.expressions
    cj = char_dir / "character.json"
    if cj.exists():
        data = json.loads(cj.read_text(encoding="utf-8"))
        ver = data.setdefault("extensions", {}).setdefault("veranima", {})
        avatar = ver.setdefault("avatar", {})
        avatar.setdefault("expressions", {}).update(mapping)
        cj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return mapping


def import_character(archive_path: Path, characters_dir: Path) -> Path:
    """.char zip → 校验 → 展开到 characters/<id>/，重名自动改名。返回角色目录。"""
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise CharacterArchiveError(f"角色包不存在: {archive_path}")
    characters_dir = Path(characters_dir)
    characters_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "r") as zf:
        _validate_zip_members(zf)
        # manifest 校验
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (KeyError, json.JSONDecodeError) as e:
            raise CharacterArchiveError(f"manifest.json 缺失或非法: {e}")
        char_id = str(manifest.get("id", "")).strip()
        if not _SAFE_ID_RE.match(char_id):
            raise CharacterArchiveError(f"非法 id: {char_id!r}（仅允许字母数字_.-）")
        display_name = str(manifest.get("display_name", "")).strip() or char_id

        # 重名自动改名（防覆盖已有角色）
        target_id, target_name = _unique_ids(char_id, display_name, characters_dir)

        target_dir = characters_dir / target_id
        temp_dir = characters_dir / f"_import_{target_id}"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir()
        try:
            zf.extractall(temp_dir)
            # character/ 根 → target_dir
            src = temp_dir / ARCHIVE_CHARACTER_ROOT.as_posix()
            if not src.is_dir():
                raise CharacterArchiveError("角色包缺少 character/ 根目录")
            shutil.copytree(src, target_dir)
            # M4_SPEC 2.3：导入时自动应用立绘说明.txt（批量绑定表情标签）
            apply_portrait_description(target_dir)
            # manifest 的 display_name 若被改名，写回 character.json
            if target_name != display_name:
                cj = target_dir / "character.json"
                if cj.exists():
                    data = json.loads(cj.read_text(encoding="utf-8"))
                    data["display_name"] = target_name
                    cj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return target_dir


def _unique_ids(char_id: str, display_name: str, characters_dir: Path) -> tuple[str, str]:
    """重名时自动加后缀（id-2 / name(2)）。"""
    target_id, target_name = char_id, display_name
    n = 2
    while (characters_dir / target_id).exists():
        target_id = f"{char_id}-{n}"
        n += 1
    n = 2
    while any(
        p.is_dir() and json.loads((p / "character.json").read_text(encoding="utf-8")).get("display_name") == target_name
        for p in characters_dir.iterdir() if (p / "character.json").exists()
    ):
        target_name = f"{display_name}({n})"
        n += 1
    return target_id, target_name

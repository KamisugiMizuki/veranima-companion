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
import mimetypes
import os
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
_CHARPKG_ALLOWED_SUFFIXES = {
    ".json", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".wav", ".ogg", ".mp3", ".flac",
}


class CharacterArchiveError(RuntimeError):
    """角色包格式错误或导入导出失败。"""


# ---------- 导出 ----------

def export_character(dir_path: Path, out_path: Path, *, package_format: str | None = None,
                     include_portraits: bool = True, include_voice: bool = False) -> Path:
    """角色目录（character.json + card.md + portraits/ + voice/）→ .char zip。

    manifest.json 自动生成：id 从角色名派生（slug 化，冲突时加哈希后缀），
    display_name 取角色名。不要求角色卡自带 id 字段（与 sakura 格式兼容）。

    .charpkg 资产开关（安卓等轻量端用；legacy 格式不受影响，始终全量）：
    - include_portraits=False：不收 portraits/（卡内表达式路径引用保留，导入端跳过缺文件校验）
    - include_voice=True：收 voice/refs 与 example_voices（供 PC 间搬运训练素材）；
      voice/models/ 因 pickle 任意代码执行风险**任何组合都不收**，训练权重走原目录复制。
    """
    dir_path = Path(dir_path)
    manifest_path = dir_path / "character.json"
    if not manifest_path.exists():
        raise CharacterArchiveError(f"角色目录缺少 character.json: {dir_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = data.get("data", data) if isinstance(data, dict) else {}
    name = str(payload.get("name") or payload.get("display_name") or dir_path.name).strip()
    char_id = dir_path.name if _SAFE_ID_RE.match(dir_path.name) else ""
    # 与 sakura 的 id 字段兼容：角色卡已有 id 则优先
    if str(payload.get("id") or "").strip():
        char_id = str(payload["id"]).strip()
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
    package_format = package_format or ("charpkg" if out_path.suffix.lower() == ".charpkg" else "legacy")
    if package_format not in {"legacy", "charpkg"}:
        raise CharacterArchiveError(f"未知角色包格式: {package_format}")
    if package_format == "legacy":
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for p in sorted(dir_path.rglob("*")):
                if p.is_file():
                    rel = PurePosixPath(ARCHIVE_CHARACTER_ROOT) / p.relative_to(dir_path).as_posix()
                    zf.write(p, rel.as_posix())
        return out_path

    files: dict[str, dict] = {}
    for p in sorted(dir_path.rglob("*")):
        if not p.is_file():
            continue
        relative = p.relative_to(dir_path)
        top = relative.parts[0]
        if top in ("voice", "example_voices") and not include_voice:
            continue
        if top == "voice" and len(relative.parts) > 1 and relative.parts[1] == "models":
            continue  # 训练权重（pickle）永不进包：导入即任意代码执行面，PC 间搬运走目录复制
        if top == "portraits" and not include_portraits:
            continue
        if p.suffix.lower() not in _CHARPKG_ALLOWED_SUFFIXES:
            raise CharacterArchiveError(f".charpkg 不允许的文件类型: {relative.as_posix()}")
        rel = (ARCHIVE_CHARACTER_ROOT / p.relative_to(dir_path).as_posix()).as_posix()
        raw = p.read_bytes()
        files[rel] = {
            "path": rel,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "media_type": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        }
    manifest = {
        "package_format": "veranima.charpkg",
        "schema_version": 1,
        "package_id": char_id,
        "character_id": char_id,
        "id": char_id,
        "display_name": name,
        "version": str(data.get("version") or "0.0.0-legacy"),
        "source": data.get("source") or {"type": "imported", "note": "由角色目录导出"},
        "license": data.get("license") or {"text": "unknown", "assets": "unknown"},
        "compatibility": {"character_card": "chara_card_v3"},
        "entrypoints": {"character": "character/character.json", "readme": "character/card.md"},
        "files": list(files.values()),
    }
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("checksums.json", json.dumps(
            {path: item["sha256"] for path, item in sorted(files.items())},
            ensure_ascii=False, indent=2,
        ))
        for path in sorted(files):
            zf.write(dir_path / PurePosixPath(path).relative_to(ARCHIVE_CHARACTER_ROOT), path)
    return out_path


# ---------- 导入 ----------

def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise CharacterArchiveError(f"角色包成员数超限（>{MAX_ARCHIVE_MEMBERS}）")
    total = 0
    seen: set[str] = set()
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        if normalized in seen:
            raise CharacterArchiveError(f"重复路径: {info.filename}")
        seen.add(normalized)
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise CharacterArchiveError(f"成员过大: {info.filename}")
        total += info.file_size
        # 路径穿越防护
        name = PurePosixPath(normalized)
        if name.is_absolute() or ".." in name.parts:
            raise CharacterArchiveError(f"非法路径: {info.filename}")
        if ":" in name.parts[0]:
            raise CharacterArchiveError(f"非法路径: {info.filename}")
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise CharacterArchiveError(f"不允许符号链接: {info.filename}")
        # 压缩比防护（zip bomb）：file_size / compress_size 超限
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO:
            raise CharacterArchiveError(f"压缩比异常（疑似 zip bomb）: {info.filename}")
    if total > MAX_ARCHIVE_TOTAL_BYTES:
        raise CharacterArchiveError("角色包总大小超限")


def apply_portrait_description(char_dir: Path) -> dict[str, str]:
    """应用立绘说明.txt（R4_SPEC 2.3）：按前缀批量绑定表情标签 → avatar.expressions。

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
        payload = data.setdefault("data", {}) if data.get("spec") == "chara_card_v3" else data
        ver = payload.setdefault("extensions", {}).setdefault("veranima", {})
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
        is_charpkg = manifest.get("package_format") == "veranima.charpkg"
        if is_charpkg:
            if manifest.get("schema_version") != 1:
                raise CharacterArchiveError("不支持的 .charpkg schema_version")
            try:
                checksums = json.loads(zf.read("checksums.json").decode("utf-8"))
            except (KeyError, json.JSONDecodeError) as e:
                raise CharacterArchiveError(f"checksums.json 缺失或非法: {e}")
            if not isinstance(checksums, dict) or not checksums:
                raise CharacterArchiveError("checksums.json 为空或非法")
            listed = set(checksums)
            actual = {n for n in zf.namelist() if n.startswith("character/") and not n.endswith("/")}
            if listed != actual:
                raise CharacterArchiveError("checksums.json 与包成员不一致")
            manifest_files = manifest.get("files")
            if not isinstance(manifest_files, list):
                raise CharacterArchiveError("manifest files 缺失或非法")
            manifest_hashes = {
                item.get("path"): item.get("sha256")
                for item in manifest_files if isinstance(item, dict)
            }
            if manifest_hashes != checksums:
                raise CharacterArchiveError("manifest files 与 checksums 不一致")
            for name, expected in checksums.items():
                suffix = PurePosixPath(name).suffix.lower()
                if suffix not in _CHARPKG_ALLOWED_SUFFIXES:
                    raise CharacterArchiveError(f".charpkg 不允许的文件类型: {name}")
                actual_hash = hashlib.sha256(zf.read(name)).hexdigest()
                if actual_hash != expected:
                    raise CharacterArchiveError(f"校验和不匹配: {name}")
        char_id = str(manifest.get("character_id") or manifest.get("id", "")).strip()
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
            # 已完成路径/链接/大小/压缩比校验；逐项写入，避免 extractall 的平台路径差异。
            for info in zf.infolist():
                rel = PurePosixPath(info.filename.replace("\\", "/"))
                if info.is_dir():
                    (temp_dir / Path(*rel.parts)).mkdir(parents=True, exist_ok=True)
                    continue
                destination = temp_dir / Path(*rel.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            # character/ 根 → target_dir
            src = temp_dir / ARCHIVE_CHARACTER_ROOT.as_posix()
            if not src.is_dir():
                raise CharacterArchiveError("角色包缺少 character/ 根目录")
            if is_charpkg:
                # 所有可失败写操作均在 staging 内完成，最后一步才原子安装。
                character_json = src / "character.json"
                if not character_json.is_file():
                    raise CharacterArchiveError(".charpkg 缺少 character/character.json")
                apply_portrait_description(src)
                _rewrite_display_name(src, target_name, display_name)
                _validate_character_tree(src)
                os.replace(src, target_dir)
            else:
                shutil.copytree(src, target_dir)
                apply_portrait_description(target_dir)
                _rewrite_display_name(target_dir, target_name, display_name)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return target_dir


def _rewrite_display_name(char_dir: Path, target_name: str, original_name: str) -> None:
    if target_name == original_name:
        return
    card_path = char_dir / "character.json"
    if not card_path.exists():
        return
    data = json.loads(card_path.read_text(encoding="utf-8"))
    payload = data.setdefault("data", {}) if data.get("spec") == "chara_card_v3" else data
    payload["display_name"] = target_name
    card_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_character_tree(char_dir: Path) -> None:
    """安装前验证角色卡可加载且本地资源引用不越界。"""
    from .character import CharacterCard

    card_path = char_dir / "character.json"
    try:
        raw = json.loads(card_path.read_text(encoding="utf-8"))
        if raw.get("spec") != "chara_card_v3":
            raise CharacterArchiveError(".charpkg character.json 必须为 chara_card_v3")
        card = CharacterCard.from_dict(raw, source=str(card_path))
    except CharacterArchiveError:
        raise
    except Exception as e:
        raise CharacterArchiveError(f"character.json 非法: {e}") from e
    if not card.name.strip():
        raise CharacterArchiveError("character.json 缺少角色名")
    root = char_dir.resolve()
    expressions = (card.veranima.get("avatar") or {}).get("expressions") or {}
    # 无 portraits/ 目录 = 立绘可选导出包（--no-portraits）：引用保留，缺文件不拦截
    has_portraits = (char_dir / "portraits").is_dir()
    for label, relative in expressions.items():
        rel = Path(str(relative))
        target = (char_dir / rel).resolve()
        if rel.is_absolute() or not target.is_relative_to(root):
            raise CharacterArchiveError(f"立绘路径越界: {label}={relative}")
        if has_portraits and not target.is_file():
            raise CharacterArchiveError(f"立绘资源缺失: {label}={relative}")


def _unique_ids(char_id: str, display_name: str, characters_dir: Path) -> tuple[str, str]:
    """重名时自动加后缀（id-2 / name(2)）。"""
    target_id, target_name = char_id, display_name
    n = 2
    while (characters_dir / target_id).exists():
        target_id = f"{char_id}-{n}"
        n += 1
    n = 2
    while any(
        p.is_dir() and _display_name(p / "character.json") == target_name
        for p in characters_dir.iterdir() if (p / "character.json").exists()
    ):
        target_name = f"{display_name}({n})"
        n += 1
    return target_id, target_name


def _display_name(card_path: Path) -> str:
    try:
        data = json.loads(card_path.read_text(encoding="utf-8"))
        payload = data.get("data", data) if isinstance(data, dict) else {}
        return str(payload.get("display_name") or payload.get("name") or "")
    except (OSError, json.JSONDecodeError):
        return ""

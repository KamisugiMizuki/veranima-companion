"""多角色注册表（DESIGN 4.11 多角色切换）：扫描 characters/ + 切换激活角色。

角色目录结构（.char 打包格式）：characters/<id>/character.json + card.md + portraits/ + voice/。
激活角色由 config.yaml 的 character_card 指定（指向 characters/<id>/character.json）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import ROOT, save_config


def roles_dir() -> Path:
    return ROOT / "characters"


def list_roles() -> list[dict]:
    """扫描 characters/，返回 [{id, name, path}]（按名称排序）。"""
    d = roles_dir()
    if not d.exists():
        return []
    roles = []
    for p in sorted(d.iterdir()):
        cj = p / "character.json"
        if not p.is_dir() or not cj.exists():
            continue
        try:
            data = json.loads(cj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        roles.append({
            "id": p.name,
            "name": str(data.get("name") or data.get("display_name") or p.name),
            "path": str(cj),
        })
    return roles


def active_role(config: dict) -> dict | None:
    """当前激活角色（config.character_card 指向）。"""
    card = config.get("character_card", "")
    if not card:
        return None
    p = Path(card)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return {
        "id": p.parent.name,
        "name": str(data.get("name") or data.get("display_name") or p.parent.name),
        "path": str(p),
    }


def switch_role(role_id: str, config: dict) -> tuple[bool, str]:
    """切换激活角色：更新 config.character_card 指向 characters/<id>/character.json。

    返回 (ok, message)。注意：切换后需重启核心生效（同设置窗口模式）。
    """
    role_dir = roles_dir() / role_id
    cj = role_dir / "character.json"
    if not role_dir.is_dir() or not cj.exists():
        return False, f"角色不存在: {role_id}"
    rel = f"characters/{role_id}/character.json"
    config["character_card"] = rel
    # 写回 config.yaml（保留原配置其余部分）
    save_config(config)
    return True, f"已切换到角色 {role_id}（重启核心生效）"


def clone_role(role_id: str, new_id: str) -> tuple[bool, str]:
    """复制角色（用于自定义新角色）。"""
    src = roles_dir() / role_id
    dst = roles_dir() / new_id
    if not src.is_dir():
        return False, f"角色不存在: {role_id}"
    if dst.exists():
        return False, f"角色已存在: {new_id}"
    shutil.copytree(src, dst)
    return True, f"已复制 {role_id} → {new_id}"

"""配置加载：config/config.yaml（存在则用），否则 config/config.example.yaml 默认值。"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | Path | None = None) -> dict:
    """加载配置。优先级：显式 path > config/config.yaml > config/config.example.yaml > {}。"""
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(ROOT / "config" / "config.yaml")
    candidates.append(ROOT / "config" / "config.example.yaml")

    for c in candidates:
        if c.exists():
            data = yaml.safe_load(c.read_text(encoding="utf-8")) or {}
            logger.info("config loaded from %s", c)
            # 相对路径基于项目根解析
            data.setdefault("root", str(ROOT))
            return data
    return {"root": str(ROOT)}


def resolve_path(config: dict, key: str) -> str:
    """将配置中的相对路径基于项目根解析为绝对路径。"""
    p = config.get(key, "")
    root = Path(config.get("root", ROOT))
    if p and not Path(p).is_absolute():
        return str(root / p)
    return p

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


def validate_config(data: dict) -> list[str]:
    """配置范围校验（R0_SPEC 6）：返回问题列表，空列表 = 通过。"""
    issues: list[str] = []
    llm = data.get("llm", {}) or {}
    max_tokens = llm.get("max_tokens")
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens <= 0):
        issues.append("llm.max_tokens 必须为正整数")
    short = llm.get("short_task_max_tokens")
    if short is not None and (not isinstance(short, int) or short < 256):
        issues.append("llm.short_task_max_tokens 必须 >=256（思考模型短任务预算）")
    out = data.get("output", {}) or {}
    max_segments = out.get("max_segments")
    if max_segments is not None and (not isinstance(max_segments, int) or not 1 <= max_segments <= 10):
        issues.append("output.max_segments 必须在 1-10")
    max_chars = out.get("max_reply_chars")
    if max_chars is not None and (not isinstance(max_chars, int) or not 100 <= max_chars <= 8000):
        issues.append("output.max_reply_chars 必须在 100-8000")
    parse_retry = out.get("parse_retry")
    if parse_retry is not None and (not isinstance(parse_retry, int) or parse_retry < 0):
        issues.append("output.parse_retry 必须为非负整数")
    return issues


def save_config(data: dict, path: str | Path | None = None) -> Path:
    """写回配置到 config/config.yaml（设置窗口用）。

    注意：yaml.dump 会丢失原文件注释——MVP 接受（配置结构简单）。
    返回写回路径。
    """
    target = Path(path) if path else ROOT / "config" / "config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in data.items() if k != "root"}
    target.write_text(
        yaml.safe_dump(safe, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    logger.info("config saved to %s", target)
    return target

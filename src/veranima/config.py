"""配置加载：config/config.yaml（存在则用），否则 config/config.example.yaml 默认值。"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

LLM_PROFILE_FIELDS = ("base_url", "model", "temperature", "max_tokens", "api_key", "timeout")
_LLM_DEFAULTS = {
    "base_url": "",
    "model": "qwen3:8b",
    "temperature": 0.8,
    "max_tokens": 4096,
    "api_key": "",
    "timeout": 120,
}


def normalize_proactive_channels(data: dict) -> dict:
    """将旧主动参数复制到独立的 QQ/pet 通道桶。"""
    proactive = data.setdefault("proactive", {}) or {}
    legacy = {
        "enabled": True,
        "max_per_day": int(proactive.get("max_per_day", 2)),
        "min_gap_minutes": int(proactive.get("min_gap_minutes", 30)),
    }
    proactive.pop("source_gap_minutes", None)
    proactive.pop("ignore_backoff", None)
    configured = proactive.get("channels") if isinstance(proactive.get("channels"), dict) else {}
    configured = {
        name: {k: v for k, v in (value or {}).items()
               if k not in {"source_gap_minutes", "ignore_backoff"}}
        for name, value in configured.items()
        if isinstance(value, dict)
    }
    defaults = {
        "qq": {**legacy, "evaluation_interval_minutes": 15, "post_silence_buffer_minutes": 30,
               "sleep_silence_hours": 8, "sleep_min_hours": 6, "sleep_max_hours": 12,
               "low_activity_multiplier": 0.3, "ignored_reply_window_hours": 24},
        "pet": dict(legacy),
    }
    proactive.pop("global_max_per_day", None)
    proactive["channels"] = {
        name: {**defaults[name], **(configured.get(name, {}) or {})}
        for name in ("qq", "pet")
    }
    return data


def _profile_id(name: str, existing: dict[str, dict]) -> str:
    normalized = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower() or "profile"
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _mask_key(value: str) -> str:
    value = str(value or "")
    return value[:4] + "****" + value[-4:] if len(value) > 8 else ("****" if value else "")


def _coerce_profile(raw: dict | None, *, name: str = "") -> dict:
    raw = raw or {}
    profile = {"name": str(raw.get("name") or name or "未命名配置").strip()}
    profile.update({
        "base_url": str(raw.get("base_url") or "").strip(),
        "model": str(raw.get("model") or _LLM_DEFAULTS["model"]).strip(),
        "temperature": float(raw.get("temperature", _LLM_DEFAULTS["temperature"])),
        "max_tokens": int(raw.get("max_tokens", _LLM_DEFAULTS["max_tokens"])),
        "api_key": str(raw.get("api_key") or ""),
        "timeout": int(raw.get("timeout", _LLM_DEFAULTS["timeout"])),
    })
    if not 0 <= profile["temperature"] <= 2:
        raise ValueError("temperature 必须在 0-2 之间")
    if profile["max_tokens"] <= 0:
        raise ValueError("max_tokens 必须为正整数")
    if profile["timeout"] <= 0:
        raise ValueError("timeout 必须为正整数")
    return profile


def normalize_llm_profiles(data: dict) -> dict:
    """将旧版单一 llm 配置规范化为可切换 profiles，并同步当前生效字段。"""
    llm = dict(data.get("llm") or {})
    raw_profiles = llm.get("profiles")
    profiles: dict[str, dict] = {}
    if isinstance(raw_profiles, dict):
        for pid, raw in raw_profiles.items():
            if isinstance(raw, dict):
                profiles[str(pid)] = _coerce_profile(raw, name=str(pid))
    if not profiles:
        profiles["default"] = _coerce_profile(llm, name="原有配置")
    active = str(llm.get("active_profile") or "default")
    if active not in profiles:
        active = next(iter(profiles))
    llm["profiles"] = profiles
    llm["active_profile"] = active
    for key in LLM_PROFILE_FIELDS:
        llm[key] = profiles[active][key]
    data["llm"] = llm
    return data


def migrate_llm_profile(data: dict, *, name: str, source: dict) -> str:
    """导入一份模型配置；已有同名配置不覆盖，返回 profile id。"""
    normalize_llm_profiles(data)
    profiles = data["llm"]["profiles"]
    pid = _profile_id(name, profiles)
    profiles[pid] = _coerce_profile(source, name=name)
    normalize_llm_profiles(data)
    return pid


def llm_profiles_payload(data: dict) -> dict:
    """返回设置页使用的配置列表，API key 只返回掩码。"""
    normalize_llm_profiles(data)
    llm = data["llm"]
    profiles = []
    for pid, raw in llm["profiles"].items():
        item = {"id": pid, "name": raw.get("name") or pid, **{k: raw[k] for k in LLM_PROFILE_FIELDS if k != "api_key"}}
        item["api_key"] = _mask_key(raw.get("api_key", ""))
        item["has_api_key"] = bool(raw.get("api_key"))
        profiles.append(item)
    current = llm["profiles"][llm["active_profile"]]
    return {
        "active_profile": llm["active_profile"],
        "profiles": profiles,
        **{k: current[k] for k in LLM_PROFILE_FIELDS if k != "api_key"},
        "api_key": _mask_key(current.get("api_key", "")),
    }


def llm_profile_action(data: dict, action: str, payload: dict | None = None) -> dict:
    """执行配置新增、修改、切换、删除；返回设置页配置快照。"""
    normalize_llm_profiles(data)
    payload = payload or {}
    llm = data["llm"]
    profiles = llm["profiles"]
    if action == "add":
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("配置名称不能为空")
        pid = _profile_id(name, profiles)
        profiles[pid] = _coerce_profile(payload, name=name)
    elif action == "update":
        pid = str(payload.get("profile_id") or "")
        if pid not in profiles:
            raise ValueError("配置不存在")
        old = profiles[pid]
        merged = {**old, **{k: payload[k] for k in LLM_PROFILE_FIELDS if k in payload}}
        if not payload.get("api_key") or "****" in str(payload.get("api_key")):
            merged["api_key"] = old.get("api_key", "")
        profiles[pid] = _coerce_profile(merged, name=str(payload.get("name") or old["name"]))
    elif action == "switch":
        pid = str(payload.get("profile_id") or "")
        if pid not in profiles:
            raise ValueError("配置不存在")
        llm["active_profile"] = pid
    elif action == "delete":
        pid = str(payload.get("profile_id") or "")
        if pid not in profiles:
            raise ValueError("配置不存在")
        if pid == llm["active_profile"]:
            raise ValueError("不能删除当前正在使用的配置")
        if len(profiles) <= 1:
            raise ValueError("至少保留一份模型配置")
        del profiles[pid]
    else:
        raise ValueError(f"不支持的配置操作：{action}")
    result = llm_profiles_payload(data)
    if action == "add":
        result["profile_id"] = pid
    return result


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
            normalize_llm_profiles(data)
            normalize_proactive_channels(data)
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
    max_chars = out.get("max_text_chars")
    if max_chars is not None and (not isinstance(max_chars, int) or not 100 <= max_chars <= 8000):
        issues.append("output.max_text_chars 必须在 100-8000")
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
    normalize_llm_profiles(data)
    normalize_proactive_channels(data)
    safe = {k: v for k, v in data.items() if k != "root"}
    target.write_text(
        yaml.safe_dump(safe, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    logger.info("config saved to %s", target)
    return target

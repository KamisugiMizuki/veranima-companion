"""VISION_SPEC 2.5/6 观察器：注视区域 → LLM 结构化 Observation。

L3 层：只输出 Observation，禁止写 memories / 调用 speak（VISION_SPEC 2 表）。
任何失败返回 Observation(summary="", category="unknown", confidence=0)，不抛给主循环。
"""
from __future__ import annotations

import json
import logging
import re
import time

from .events import Observation

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
CATEGORIES = ("coding", "browser", "game", "video", "meeting", "private", "unknown")


def _parse_observation(resp: str) -> dict:
    """VISION_SPEC 6 契约：去 fence → JSON 解析 → 字段白名单。"""
    cleaned = _FENCE_RE.sub("", (resp or "").strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    summary = str(data.get("summary") or "").strip()
    out["summary"] = summary[:80]  # 不超过 80 字
    category = str(data.get("category") or "unknown").strip()
    out["category"] = category if category in CATEGORIES else "unknown"
    notable = data.get("notable") or []
    out["notable"] = [str(x)[:40] for x in notable][:3]  # 最多 3 项
    try:
        out["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["sensitive_redacted"] = bool(data.get("sensitive_redacted", False))
    return out


def observe(llm, region_b64: str, *, window_title: str = "",
            category_hint: str = "") -> Observation:
    """L3 观察（VISION_SPEC 5/6）：区域图 → Observation。

    失败（LLM 异常/空响应/解析失败）→ Observation(unknown, 0)，静默降级。
    """
    if not region_b64 or llm is None:
        return Observation(category="unknown", confidence=0.0)
    prompt = (
        "简要描述这张屏幕截图里发生了什么。"
        '只输出 JSON：{"summary":"不超过80字的事实描述",'
        '"category":"coding|browser|game|video|meeting|private|unknown",'
        '"notable":["最多3项"],"confidence":0.0,"sensitive_redacted":false}'
    )
    if window_title:
        prompt = f"（当前前台窗口：{window_title}）" + prompt
    if category_hint:
        prompt = f"（窗口类别：{category_hint}）" + prompt
    try:
        resp = llm.observe_image(region_b64, prompt=prompt)
    except Exception as e:
        logger.warning("observe failed: %s", e)
        return Observation(category="unknown", confidence=0.0)
    if not resp:
        return Observation(category="unknown", confidence=0.0)
    data = _parse_observation(resp)
    if not data.get("summary") or data.get("category") == "unknown":
        return Observation(category="unknown", confidence=0.0)
    return Observation(
        summary=data["summary"],
        category=data["category"],
        notable=tuple(data.get("notable", [])),
        confidence=data.get("confidence", 0.0),
        sensitive_redacted=data.get("sensitive_redacted", False),
        expires_at=time.time() + 600,  # TTL 10min（VISION_SPEC 5）
    )

"""VISION_SPEC 2.5 观察器：注视区域 → LLM 理解（低 token 小图）。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def observe_region(llm, region_b64: str, *, window_title: str = "") -> tuple[str, str]:
    """LLM 理解注视区域（复用 llm.observe_image 多模态链路）。

    返回 (tag, note)。失败返回 ("", "")（调用方静默降级）。
    """
    if not region_b64 or llm is None:
        return "", ""
    prompt = (
        "简要描述这张屏幕截图里发生了什么（50字内）。"
        '只输出 JSON：{"observe": "描述", "tag": "类别(游戏/办公/浏览器/其他)"}'
    )
    if window_title:
        prompt = f"（当前前台窗口：{window_title}）" + prompt
    try:
        resp = llm.observe_image(region_b64, prompt=prompt)
    except Exception as e:
        logger.warning("observe_region failed: %s", e)
        return "", ""
    if not resp:
        return "", ""
    # 容错解析（剥 fence + 截 JSON）
    import json
    import re
    tag, note = "", resp
    cleaned = resp.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        tag = str(data.get("tag") or "")
        note = str(data.get("observe") or resp)
    except json.JSONDecodeError:
        pass
    return tag, note

"""M4 表情标签驱动（M4_SPEC 2.1）：LLM 结构化回复解析。

channel=tts 时 prompt 要求 JSON 输出：
  {"segments":[{"text":"回复内容","tone":"中性","portrait":"开心脸红"}]}
解析失败降级：整段当纯文本（portrait/tone 空），不重试不报错。
"""
from __future__ import annotations

import json
import re

_SEGMENTS_RE = re.compile(r"\{[^{}]*\"text\"[^{}]*\}")


def extract_segments(reply: str) -> tuple[str, str, str]:
    """从 LLM 回复提取 (text, tone, portrait)。失败时 text=原文，tone/portrait 空。"""
    text, tone, portrait = reply, "", ""
    try:
        # 直接 JSON 解析
        data = json.loads(reply)
        segs = data.get("segments") if isinstance(data, dict) else None
        if not isinstance(segs, list) or not segs:
            return text, tone, portrait
        first = segs[0] if isinstance(segs[0], dict) else {}
        text = str(first.get("text") or "").strip() or reply  # 缺 text 只回退文本
        tone = str(first.get("tone") or "").strip()
        portrait = str(first.get("portrait") or "").strip()
    except json.JSONDecodeError:
        # 容错：模型可能包了 markdown 代码块或多段，找第一个含 text 的对象
        m = _SEGMENTS_RE.search(reply)
        if m:
            try:
                obj = json.loads(m.group(0))
                text = str(obj.get("text") or reply).strip()
                tone = str(obj.get("tone") or "").strip()
                portrait = str(obj.get("portrait") or "").strip()
            except json.JSONDecodeError:
                pass
    return text, tone, portrait

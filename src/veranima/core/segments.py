"""R2 表情标签驱动（R2_SPEC 2）：LLM 结构化回复解析。

本模块是兼容 facade：内部实现已迁移到 `core/reply.py`（R0_SPEC 4 确定性解析）。
保留 `extract_segments(reply, bilingual)` 旧签名，返回 (text, tone, portrait, ja_text)。

解析失败降级：整段当纯文本（portrait/tone 空），不重试不报错。
"""
from __future__ import annotations

from .reply import parse_reply

__all__ = ["extract_segments"]


def extract_segments(reply: str, *, bilingual: bool = False,
                     card: object = None) -> tuple[str, str, str, str]:
    """从 LLM 回复提取 (text, tone, portrait, ja_text)。

    双语模式（bilingual=True，R2 由岐日语配音）：segment 含 ja/zh 两个文本——
      {"segments":[{"ja":"日本語","zh":"中文","tone":"...","portrait":"..."}]}
    返回的 text = zh（显示），ja_text = ja（送 TTS）。非双语时 ja_text 空。
    失败时 text=原文，tone/portrait/ja_text 空。
    """
    parsed = parse_reply(
        reply,
        channel="tts",
        card=card,
        bilingual=bilingual,
        max_segments=1,
        max_chars=1200,
    )
    if parsed.degraded:
        return reply, "", "", ""  # 兼容旧接口：失败时 tone/portrait/ja 为空
    seg = parsed.segments[0] if parsed.segments else None
    if seg is None:
        return reply, "", "", ""
    # 缺 text 字段：文本回退原文，tone/portrait 保留（旧 facade 行为）
    return seg.text or reply, seg.tone, seg.portrait, seg.ja_text
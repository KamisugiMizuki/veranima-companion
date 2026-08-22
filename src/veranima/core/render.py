"""IM 通道渲染器（DESIGN 4.8）：发送前机械规则后处理。

prompt 引导负责「生成时就带通道风格」，这里只做机械可逆的修正：
- 感叹号限频（每段最多 1 个，多余降级为句号）
- 波浪号亲密度阈值（attachment < 0.8 时替换为句号）
- 连续换行压缩（3+ 空行压成 1 个——禁止用空行模拟「正在输入」）
- 表情限频（emoji_frequency=never 时全删；low/high 靠 prompt 引导）

纯函数、无 IO、无状态，R2 只有 IM 渲染器；TTS 渲染器见 R2_SPEC 3.Renderer 接口。
"""
from __future__ import annotations

import re

# 波浪号（含全角/半角变体）
_TILDE = re.compile(r"[~～]")
# 连续换行（3 个及以上空行）
_MULTI_NL = re.compile(r"\n{3,}")
# 常见 emoji 范围（含变体选择符 U+FE0F）
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF\uFE0F\u2764\u2B50]"
)
_URL = re.compile(r"https?://[^\s)）]+")


def _limit_exclamations(text: str) -> str:
    """每段（\n\n 分割）最多保留 1 个感叹号，多余替换为句号。"""
    out = []
    for para in text.split("\n\n"):
        if para.count("！") + para.count("!") <= 1:
            out.append(para)
            continue
        seen = False
        chars = []
        for ch in para:
            if ch in ("！", "!"):
                if seen:
                    chars.append("。")
                else:
                    seen = True
                    chars.append(ch)
            else:
                chars.append(ch)
        out.append("".join(chars))
    return "\n\n".join(out)


def _strip_tildes_below_threshold(text: str, attachment: float) -> str:
    """波浪号仅亲密度 ≥0.8 允许（DESIGN 4.8 亲密度阈值差异化）。"""
    if attachment >= 0.8:
        return text
    return _TILDE.sub("。", text)


def _compress_newlines(text: str) -> str:
    """连续 3+ 空行压成 1 个（禁止模拟「正在输入」）。"""
    return _MULTI_NL.sub("\n\n", text)


def _strip_emoji(text: str) -> str:
    """emoji_frequency=never：全删表情符号。"""
    return _EMOJI.sub("", text)


def render_im(reply, state=None, **old_kwargs) -> str:
    """IM 通道渲染入口（R2_SPEC 3）：`render_im(reply, state) -> str`。

    兼容旧调用 `render_im(text, attachment=..., emoji_frequency=...)`：
    传 Reply 时读 reply.text + state.attachment + 角色卡 emoji_frequency。
    规则顺序：换行压缩 → 波浪号 → 感叹号 → 表情。
    只做可逆清理，不随机改写事实（R2_SPEC 3）。
    """
    from .reply import Reply, strip_echoed_time_prefixes

    if isinstance(reply, Reply):
        text = reply.text
        attachment = state.attachment if state is not None else 0.5
        emoji_frequency = "low"
        try:
            emoji_frequency = (reply._card.veranima or {}).get("emoji_frequency", "low") \
                if getattr(reply, "_card", None) else "low"
        except Exception:
            pass
    else:
        # 旧签名兼容（text 字符串）
        text = reply
        attachment = old_kwargs.get("attachment", 0.5)
        emoji_frequency = old_kwargs.get("emoji_frequency", "low")

    t = strip_echoed_time_prefixes(_compress_newlines(text))
    t = _strip_tildes_below_threshold(t, float(attachment))
    t = _limit_exclamations(t)
    if emoji_frequency == "never":
        t = _strip_emoji(t)
    return t.strip()


def render_tts(reply, state=None) -> list:
    """TTS 通道渲染（R2_SPEC 3）：`render_tts(reply, state) -> list[SpeechSegment]`。

    每个 ReplySegment → SpeechSegment：
    - 双语：text=ja（送 TTS），display_text=zh 或 translation（气泡显示）
    - 单语：text=原文，display_text 空
    - suppress_tts（缺 ja）：仍生成 display_text 供静默显示，调用方不合成
    """
    from .reply import SpeechSegment

    out = []
    for seg in reply.segments:
        if seg.suppress_tts:
            out.append(SpeechSegment(
                text=_URL.sub("", seg.ja_text or seg.text).strip(), tone=seg.tone, portrait=seg.portrait,
                display_text=seg.translation or seg.text,
                suppress_tts=True,
            ))
        elif seg.ja_text:
            out.append(SpeechSegment(
                text=_URL.sub("", seg.ja_text).strip(), tone=seg.tone, portrait=seg.portrait,
                display_text=seg.translation or seg.text,
            ))
        else:
            out.append(SpeechSegment(text=_URL.sub("", seg.text).strip(), tone=seg.tone, portrait=seg.portrait))
    return out

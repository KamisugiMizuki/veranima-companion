"""IM 通道渲染器（DESIGN 4.8）：发送前机械规则后处理。

prompt 引导负责「生成时就带通道风格」，这里只做机械可逆的修正：
- 感叹号限频（默认每段最多 1 个；arousal 高=情绪激动时放宽到 3 个）
- 波浪号亲密度阈值（attachment < 0.8 时删掉）
- 句尾句号删除（网络聊天不打句尾句号——09-08 实测角色 91% 消息带句尾标点、
  用户 17%，句号是「书面腔」最大来源）
- 连续换行压缩（3+ 空行压成 1 个——禁止用空行模拟「正在输入」）
- 表情限频（emoji_frequency=never 时全删；low/high 靠 prompt 引导）

09-08 用户裁决「情绪太平稳」后的修正：限频只删不替换。旧实现把多余的
感叹号/波浪号降级成句号，等于把情绪改写成书面语——与「多用标点、气气气」
的表达目标相反。

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

# arousal ≥ 此值视为「情绪激动」：感叹号限频放宽（09-08 Q2）
_HOT_AROUSAL = 0.62


def _limit_exclamations(text: str, keep: int = 1) -> str:
    """每段（\\n\\n 分割）最多保留 keep 个感叹号，多余的直接删掉。

    只删不替换：旧版降级成句号会把情绪改成书面腔（09-08 实锤）。
    """
    out = []
    for para in text.split("\n\n"):
        seen = 0
        chars = []
        for ch in para:
            if ch in ("！", "!"):
                seen += 1
                if seen > keep:
                    continue
            chars.append(ch)
        out.append("".join(chars))
    return "\n\n".join(out)


def _strip_tildes_below_threshold(text: str, attachment: float) -> str:
    """波浪号仅亲密度 ≥0.8 允许；不达标直接删（旧版替换成句号=书面腔）。"""
    if attachment >= 0.8:
        return text
    return _TILDE.sub("", text)


def _trim_trailing_period(text: str) -> str:
    """删掉每行末尾的句号。

    网络聊天几乎不打句尾句号；保留「。。」这类情绪重复。
    """
    lines = []
    for line in text.split("\n"):
        stripped = line.rstrip()
        if stripped.endswith("。") and not stripped.endswith("。。"):
            stripped = stripped[:-1]
        lines.append(stripped)
    return "\n".join(lines)


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
    规则顺序：换行压缩 → 波浪号 → 感叹号 → 句尾句号 → 表情。
    只做可逆清理，不随机改写事实（R2_SPEC 3）。
    """
    from .reply import Reply, strip_echoed_time_prefixes, strip_internal_prompt_leak, strip_thinking_trace

    hot = False
    if isinstance(reply, Reply):
        text = reply.text
        attachment = state.attachment if state is not None else 0.5
        hot = float(getattr(state, "arousal", 0.5) or 0.5) >= _HOT_AROUSAL if state is not None else False
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

    t = strip_thinking_trace(strip_internal_prompt_leak(strip_echoed_time_prefixes(_compress_newlines(text))))
    t = _strip_tildes_below_threshold(t, float(attachment))
    t = _limit_exclamations(t, keep=3 if hot else 1)
    t = _trim_trailing_period(t)
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

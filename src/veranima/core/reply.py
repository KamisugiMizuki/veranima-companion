"""R0 统一 Reply 契约（R0_SPEC 4）：LLM 输出 → 确定性结构化回复。

IM：纯文本 → 一个 ReplySegment(text=raw)。
TTS：JSON segments → 最多 max_segments 段，逐段校验/截断/回退。

解析步骤必须确定性（R0_SPEC 4.8 不依赖"第一个正则命中"）：
  1. trim
  2. 去 ```json fence
  3. json.loads
  4. 读 segments 数组（最多 max_segments）
  5. 每段 text/ja/zh 截断（总预算 max_reply_chars）
  6. tone/portrait 只接受角色卡白名单，否则回退 中性/闲置
  7. 双语缺 ja：segment 标记 suppress_tts=True，显示翻译
  8. 失败：从原文提取可读文本；完全失败返回 degraded
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class ReplySegment:
    text: str
    translation: str = ""        # 双语：显示用翻译（zh）
    ja_text: str = ""            # 双语：TTS 用原文（ja）；空且 bilingual → suppress_tts
    tone: str = "中性"
    portrait: str = ""
    suppress_tts: bool = False


@dataclass
class SpeechSegment:
    """TTS 渲染结果（R2_SPEC 3）：display_text 与语音必须同一 segment。"""
    text: str            # 送 TTS 合成的文本（ja）
    tone: str = "中性"
    portrait: str = ""
    display_text: str = ""   # 气泡显示文本（zh）；空 = 用 text
    suppress_tts: bool = False


@dataclass
class Reply:
    segments: list[ReplySegment] = field(default_factory=list)
    stance: str = ""
    follow_up: str = "none"      # none/answer/invite/close
    memory_candidates: list[dict] = field(default_factory=list)
    degraded: str = ""           # 非空 = 解析失败降级原因

    @property
    def text(self) -> str:
        """主显示文本（第一段）。"""
        return self.segments[0].text if self.segments else ""

    @property
    def tone(self) -> str:
        return self.segments[0].tone if self.segments else "中性"

    @property
    def portrait(self) -> str:
        return self.segments[0].portrait if self.segments else ""

    @property
    def ja_text(self) -> str:
        return self.segments[0].ja_text if self.segments else ""


def _strip_fence(raw: str) -> str:
    return _FENCE_RE.sub("", raw).strip()


def _clean_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def _valid_tone(tone: str, card_tones: list[str] | None) -> str:
    if card_tones is None:
        return tone  # 无角色卡白名单时放行（facade 兼容旧行为）
    if tone and tone in card_tones:
        return tone
    return "中性"


def _valid_portrait(portrait: str, card: Any) -> str:
    """portrait 必须在角色卡 avatar.expressions 词表内（R0_SPEC 4.6 白名单）。"""
    if not portrait:
        return ""
    try:
        exprs = (card.veranima or {}).get("avatar", {}).get("expressions", {})
    except AttributeError:
        return portrait  # 无角色卡 → 无词表可校验，放行
    if not exprs:
        return portrait  # 角色卡无 expressions 词表 → 放行
    return portrait if portrait in exprs else ""


def _segments_from_data(data: dict, *, bilingual: bool, max_segments: int,
                        max_chars: int, card_tones: list[str], card: Any) -> list[ReplySegment]:
    segs = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segs, list):
        return []
    out: list[ReplySegment] = []
    for item in segs[:max_segments]:
        if not isinstance(item, dict):
            continue
        if bilingual:
            zh = _clean_text(item.get("zh") or item.get("text"), max_chars)
            ja = _clean_text(item.get("ja"), max_chars)
            if not zh and not ja:
                continue
            display = zh or ja
            seg = ReplySegment(
                text=display,
                translation=zh,
                ja_text=ja,
                tone=_valid_tone(str(item.get("tone") or "").strip(), card_tones),
                portrait=_valid_portrait(str(item.get("portrait") or "").strip(), card),
                suppress_tts=not bool(ja),
            )
        else:
            text = _clean_text(item.get("text"), max_chars)
            seg = ReplySegment(
                text=text,
                tone=_valid_tone(str(item.get("tone") or "").strip(), card_tones),
                portrait=_valid_portrait(str(item.get("portrait") or "").strip(), card),
            )
        out.append(seg)
    return out


def _fallback_text(raw: str, max_chars: int) -> Reply:
    """解析失败：从原文提取可读文本（去 fence、strip 后截断）。

    tone/portrait 置空：纯文本 fallback 无结构化标签；「回退中性」只适用于
    JSON 内 tone 非法的情况（R0_SPEC 4.6）。
    """
    cleaned = _strip_fence(raw)
    if cleaned:
        cleaned = cleaned[:max_chars]
        return Reply(segments=[ReplySegment(text=cleaned, tone="", portrait="")])
    return Reply(degraded="empty_output")


def parse_reply(raw: str, *, channel: str = "im", card: Any = None,
                bilingual: bool = False, max_segments: int = 6,
                max_chars: int = 1200, card_tones: list[str] | None = None) -> Reply:
    """确定性解析 LLM 回复（R0_SPEC 4）。

    channel="im"：纯文本 → 单段。
    channel="tts"：JSON segments；失败降级纯文本；完全失败 degraded。
    """
    raw = raw.strip()
    if not raw:
        return Reply(degraded="empty_output")

    if card_tones is None:
        card_tones = list(card.tones) if card is not None else None

    if channel != "tts":
        return Reply(segments=[ReplySegment(text=raw[:max_chars])])

    # TTS：确定性 JSON 解析
    cleaned = _strip_fence(raw)
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("root not object")
        segs = _segments_from_data(
            data, bilingual=bilingual, max_segments=max_segments,
            max_chars=max_chars, card_tones=card_tones, card=card,
        )
        if segs:
            # R2_SPEC 2：顶层 stance/follow_up/memory_candidates 提取
            stance = str(data.get("stance") or "").strip()
            follow_up = str(data.get("follow_up") or "none").strip()
            if follow_up not in ("none", "answer", "ask", "offer", "remind", "invite", "close"):
                follow_up = "none"
            mcs = data.get("memory_candidates")
            candidates = []
            if isinstance(mcs, list):
                for mc in mcs[:5]:
                    if isinstance(mc, dict) and mc.get("content"):
                        candidates.append({
                            "content": str(mc["content"])[:500],
                            "kind": str(mc.get("kind") or "user_fact"),
                        })
            return Reply(segments=segs, stance=stance, follow_up=follow_up,
                         memory_candidates=candidates)
        return _fallback_text(raw, max_chars)
    except (json.JSONDecodeError, ValueError):
        # 容错：模型可能输出残缺/重复 JSON —— 用正则找候选对象逐个尝试
        for m in re.finditer(r"\{[^{}]*\"(?:text|ja|zh)\"[^{}]*\}", cleaned):
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            segs = _segments_from_data(
                {"segments": [obj]}, bilingual=bilingual, max_segments=max_segments,
                max_chars=max_chars, card_tones=card_tones, card=card,
            )
            if segs:
                return Reply(segments=segs)
        return _fallback_text(raw, max_chars)

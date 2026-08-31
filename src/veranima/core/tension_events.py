from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TensionEventCandidate:
    event_type: str
    base_delta: float
    reason: str
    confidence: float = 0.9
    context_factor: float = 1.0
    dedupe_suffix: str = ""


def extract_direct_question(text: str) -> str | None:
    text = str(text or "").strip()
    if not text:
        return None
    if "？" not in text and "?" not in text:
        return None
    if not any(token in text for token in ("吗", "有没有", "是否", "怎么", "为什么", "后来", "能不能", "可不可以")):
        return None
    return text


def _question_terms(text: str) -> set[str]:
    text = re.sub(r"[，。！？!?、：:；;（）()\s]", "", str(text or ""))
    terms = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    for i in range(len(text) - 1):
        terms.add(text[i:i + 2])
    return terms


def _answers_question(answer: str, question: str) -> bool:
    answer_text = str(answer).strip()
    answer_terms = _question_terms(answer_text)
    question_terms = _question_terms(question)
    if not answer_terms or not question_terms:
        return False
    overlap = len(answer_terms & question_terms)
    if overlap >= 1 and len(answer_text) >= 2:
        return True
    action_words = ("试", "做", "跑", "看", "查", "吃", "去", "会", "能", "有", "没")
    question_actions = [word for word in action_words if word in question]
    answer_signals = ("了", "过", "已经", "可以", "能", "没有", "没", "还没", "不知道")
    return bool(question_actions and any(word in answer_text for word in question_actions)
                and any(signal in answer_text for signal in answer_signals))


def classify_user_tension_event(
    text: str,
    *,
    new_conversation: bool = False,
    direct_question: str | None = None,
    judgment: str | None = None,
) -> TensionEventCandidate | None:
    """TV 事件分类。judgment=统一判断点的 tension 字段
    （answered/skipped/low_investment/none；None=未裁决退回词面+重合度规则）。
    裁决为 None（模型明说"没回应问题/不算敷衍"）→ 直接不产生事件，
    堵住原规则"回复≥2字且重合度低就 +5"的误伤。"""
    text = str(text or "").strip()
    if not text:
        return None
    if judgment not in ("answered", "skipped", "low_investment", "none"):
        judgment = None
    if judgment == "none":
        return None
    if judgment == "answered":
        return TensionEventCandidate("answered_question", -8.0, "用户认真回应了直接问题（判断点）", 0.85)
    if judgment == "skipped":
        return TensionEventCandidate("question_skipped", 5.0, "用户回复但没有回应直接问题（判断点）", 0.8)
    if judgment == "low_investment":
        return TensionEventCandidate("terse_streak", 3.0, "明显敷衍且上文需要展开（判断点）", 0.75)
    # judgment is None → 原规则兜底
    if any(token in text for token in ("别主动找我", "不要主动找我", "不要打扰", "别打扰我")):
        return TensionEventCandidate("explicit_pause", 0.0, "用户明确要求不要主动联系", 0.99)
    if any(token in text for token in ("可以主动找我", "恢复主动", "解除免打扰")):
        return TensionEventCandidate("explicit_resume", 0.0, "用户明确恢复主动联系", 0.99)
    if new_conversation:
        return TensionEventCandidate("user_initiated", -5.0, "用户主动开启对话", 0.9)
    if direct_question:
        if text == direct_question.strip():
            return None
        if _answers_question(text, direct_question):
            return TensionEventCandidate("answered_question", -8.0, "用户认真回应了直接问题", 0.8)
        if len(text) >= 2:
            return TensionEventCandidate("question_skipped", 5.0, "用户回复但没有回应直接问题", 0.7)
    return None


def classify_low_investment_streak(rows: list[dict], current_text: str) -> TensionEventCandidate | None:
    """只在连续三次确实需要展开的上下文中标记低投入；短回复本身不是过错。"""
    if any(token in str(current_text or "") for token in ("忙", "晚点", "之后聊", "先不说")):
        return None
    short = {"嗯", "哦", "好", "行", "对", "啊"}
    pairs: list[tuple[str, str]] = []
    for index in range(len(rows) - 1):
        assistant = rows[index]
        user = rows[index + 1]
        if assistant.get("role") != "assistant" or user.get("role") != "user":
            continue
        if str(user.get("content") or "").strip() in short:
            pairs.append((str(assistant.get("content") or ""), str(user.get("content") or "")))
    if len(pairs) < 2 or str(current_text or "").strip() not in short:
        return None
    expandable = ("吗", "怎么", "为什么", "愿意", "说说", "怎么看", "后来", "有没有")
    if all(any(token in assistant for token in expandable) for assistant, _ in pairs[-2:]):
        return TensionEventCandidate("terse_streak", 3.0, "连续三次低投入回复且前文确实需要展开", 0.72)
    return None
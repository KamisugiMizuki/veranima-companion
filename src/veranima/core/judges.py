"""统一消息判断点（2026-08-31 面向样例编程清算）。

一条用户消息一次低成本 LLM 调用，产出本轮全部语义判断（搜索/场景/状态/
回应/冲突/记忆/情绪/追问/任务/投入度）；每个字段独立回退到现有关键词规则
（判断点哲学：关键词=预筛+兜底，语义裁决归 LLM）。LLM 挂掉=逐项退回规则，
行为与改造前完全一致，不新增故障面。

消费者一律通过 Agent.turn_judgment() 读取，禁止再在语义判断上扩词表。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_VALID_SCENES = {"normal", "busy", "away"}
_VALID_USER_STATES = {"normal", "sleeping", "busy", "low_mood", "away", "closing"}
_VALID_MEMORIES = {"none", "event", "preference", "commitment"}
_VALID_EMOTIONS = {"none", "happy", "sad", "angry", "anxious"}
_VALID_TENSION = {"none", "answered", "skipped", "low_investment"}
_VALID_CONFLICT = {"none", "apology", "violation"}


@dataclass
class MessageJudgment:
    wants_search: bool | None = None      # None=LLM 未裁决（消费者用规则）
    scene: str | None = None              # 用户场景信号：normal/busy/away
    user_state: str | None = None         # QQ 用户状态机信号
    tension: str | None = None            # answered/skipped/low_investment（需上轮问句语境）
    conflict: str | None = None           # apology/violation
    memory_kind: str = "none"             # event/preference/commitment/none
    emotion: str = "none"                 # happy/sad/angry/anxious/none
    clarification: bool | None = None     # 追问细节（R1 精确值开关）
    is_task: bool | None = None           # 委托任务意图（R5 工单入口）
    wants_remember: bool | None = None    # 主动回溯共同往事（P-6 remember）
    feedback_like: bool | None = None     # 对上一条回复正向（喜欢/认可）
    feedback_dislike: bool | None = None  # 对上一条回复负向（嫌弃/纠正风格内容）
    investment_note: str = ""             # 判定理由（日志/调试，不出口）


@dataclass
class JudgeConfig:
    enabled: bool = True
    max_chars: int = 220                  # 送判文本截断
    history_note_max: int = 120


def build_judge_prompt(text: str, prev_assistant: str) -> str:
    ctx = f"\n上一条助手消息（可能为空）：{prev_assistant[:120]}" if prev_assistant else ""
    return (
        "分析下面这条用户消息（来自聊天伴侣场景，用户=对话对象，角色=助手）。"
        f"消息：{text[:220]}{ctx}\n"
        "只输出一个 JSON，字段与含义：\n"
        '  "wants_search": 用户是否希望助手去查外部/最新信息才答得好（含"我不确定你知道吗"'
        '式的时事话题；闲聊、感受、约定、问助手本人=否）,\n'
        '  "scene": 用户当下状态对打扰的影响——"busy"正在做事不便聊/'
        '"away"要离开或已离开（睡、出门、洗澡、吃饭途中）/"normal"其余,\n'
        '  "user_state": 更细的用户状态标签："sleeping"去睡了/"busy"忙（开会赶工）'
        '/"low_mood"情绪低落想静静/"away"外出/"closing"道别收尾/"normal",\n'
        '  "tension": 若有上一条助手消息：用户"answered"正面回应了其中的问题/'
        '"skipped"答非所问/"low_investment"明显敷衍（连续短句且上文需要展开）/"none",\n'
        '  "conflict": 用户在"apology"道歉/示好化解（包括变体如"话说重了""刚才是我急"'
        '）/"violation"指出助手越界令其不适（包括变体如"过了""适可而止""笑不出来"）/'
        '"none",\n'
        '  "memory": 值得长期记住的事实——"event"发生的事/"preference"喜好厌恶忌口'
        '（含变体如"无辣不欢""一口就劝退"）/"commitment"约定或要求提醒/'
        '"none"不值得记,\n'
        '  "emotion": 用户情绪："happy"/"sad"/"angry"/"anxious"焦虑压力/"none",\n'
        '  "clarification": 用户是否在对助手刚才的模糊/错误回答追问精确细节,\n'
        '  "wants_remember": 用户是否想和助手共同回忆往事（"还记得…"类，含变体）,\n'
        '  "is_task": 是否委托助手做一件需要动手执行的事（查文件/写东西/整理/下载'
        '，含不带"帮我"字样的说法）,\n'
        '  "feedback_like": 若有上一条助手消息且用户这句在认可/喜欢它；'
        '"feedback_dislike": 用户在嫌弃/纠正它的内容或风格（"太长了""别这样回"）；'
        '无关联两者都 false。\n'
        "判定看语义不看字面；拿不准就选最保守值（none/false）。只输出 JSON。"
    )


def _coerce(raw: dict) -> MessageJudgment:
    j = MessageJudgment()
    if isinstance(raw.get("wants_search"), bool):
        j.wants_search = raw["wants_search"]
    scene = str(raw.get("scene") or "")
    j.scene = scene if scene in _VALID_SCENES else None
    us = str(raw.get("user_state") or "")
    j.user_state = us if us in _VALID_USER_STATES else None
    tv = str(raw.get("tension") or "")
    j.tension = tv if tv in _VALID_TENSION else None
    cf = str(raw.get("conflict") or "")
    j.conflict = cf if cf in _VALID_CONFLICT else None
    mk = str(raw.get("memory") or "")
    j.memory_kind = mk if mk in _VALID_MEMORIES else "none"
    em = str(raw.get("emotion") or "")
    j.emotion = em if em in _VALID_EMOTIONS else "none"
    if isinstance(raw.get("clarification"), bool):
        j.clarification = raw["clarification"]
    if isinstance(raw.get("wants_remember"), bool):
        j.wants_remember = raw["wants_remember"]
    if isinstance(raw.get("is_task"), bool):
        j.is_task = raw["is_task"]
    if isinstance(raw.get("feedback_like"), bool):
        j.feedback_like = raw["feedback_like"]
    if isinstance(raw.get("feedback_dislike"), bool):
        j.feedback_dislike = raw["feedback_dislike"]
    return j


def judge_message(llm, text: str, prev_assistant: str = "",
                  *, config: dict | None = None) -> MessageJudgment | None:
    """一次调用产全量判断。返回 None=不可用/不值得判（调用方全量回退规则）。

    预筛（控成本非裁决）：<6 字极短消息不送判，但含场景词（要睡了/去忙等）
    例外——这类消息正是闸门信号，等词表兜底会留语义空窗。
    """
    from .ambient import SCENE_KEYWORDS
    cfg = config or {}
    key = str(text or "").strip()
    if not cfg.get("enabled", True) or not key:
        return None
    if len(key) < 6 and not any(kw in key for group in SCENE_KEYWORDS.values() for kw in group):
        return None
    if llm is None or not getattr(llm, "is_model_loaded", lambda: False)():
        return None
    try:
        raw = llm.chat_structured(
            [{"role": "user", "content": build_judge_prompt(text, prev_assistant)}],
            temperature=0.1,  # 预算不传=全局上限（12 字段 JSON+reasoning，512 实测被烧空）
        )
        data = json.loads(str(raw).strip().strip("`").removeprefix("json").strip())
        if not isinstance(data, dict):
            return None
        j = _coerce(data)
        j.investment_note = str(data.get("_why") or "")[:80]
        return j
    except Exception as e:
        logger.debug("message judge failed (rule fallback): %s", e)
        return None

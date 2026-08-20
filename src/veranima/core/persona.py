"""PERSONA_LOOP_SPEC 人格循环：候选提取、校验与转换（P-1 起）。

本模块只产结构化候选并交给 MemoryStore 校验/写入；不直接写 SQL，
不修改 Character Core，不依赖 LLM 的唯一决策。
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 人格候选 kind（PERSONA_LOOP_SPEC 4 数据映射）
PERSONA_KINDS = (
    "user_framework", "character_belief", "shared_meaning",
    "relationship_event", "interaction_rule",
)

# kind → 默认置信度（PERSONA_LOOP_SPEC 15 转换契约）
KIND_DEFAULT_CONFIDENCE = {
    "user_framework": 0.60,
    "character_belief": 0.55,
    "shared_meaning": 0.65,
    "relationship_event": 0.70,
    "interaction_rule": 0.80,
}


@dataclass
class PersonaCandidate:
    """P-1 候选 DTO：证据齐备才可进入校验；id/版本由程序补写。"""

    kind: str
    title: str
    content: str
    evidence_message_ids: list[int] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)
    confidence: float = 0.6
    stability: float = 0.5
    importance: float = 0.6
    emotional_weight: float = 0.5
    user_confirmed: bool = False
    role_compatible: bool = True
    needs_confirmation: bool = True
    subject: str = "user"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "evidence_message_ids": list(self.evidence_message_ids),
            "scope": list(self.scope),
            "confidence": round(float(self.confidence), 4),
            "stability": round(float(self.stability), 4),
            "importance": round(float(self.importance), 4),
            "emotional_weight": round(float(self.emotional_weight), 4),
            "user_confirmed": bool(self.user_confirmed),
            "role_compatible": bool(self.role_compatible),
            "needs_confirmation": bool(self.needs_confirmation),
            "subject": self.subject,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonaCandidate":
        """缺字段用默认；类型错误拒绝（抛 ValueError，不静默转换）。"""
        kind = data.get("kind")
        if kind not in PERSONA_KINDS:
            raise ValueError(f"unknown persona kind: {kind!r}")
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content 必须为非空字符串")
        ev = data.get("evidence_message_ids")
        if not isinstance(ev, list) or not all(isinstance(x, int) for x in ev):
            raise ValueError("evidence_message_ids 必须为 int 列表")
        title = data.get("title", "")
        if not isinstance(title, str):
            raise ValueError("title 必须为字符串")
        return cls(
            kind=kind,
            title=title,
            content=content,
            evidence_message_ids=list(ev),
            scope=[s for s in data.get("scope", []) if isinstance(s, str)],
            confidence=_clamp01(data.get("confidence", 0.6)),
            stability=_clamp01(data.get("stability", 0.5)),
            importance=_clamp01(data.get("importance", 0.6)),
            emotional_weight=_clamp01(data.get("emotional_weight", 0.5)),
            user_confirmed=bool(data.get("user_confirmed", False)),
            role_compatible=bool(data.get("role_compatible", True)),
            needs_confirmation=bool(data.get("needs_confirmation", True)),
            subject=data.get("subject", "user") if isinstance(data.get("subject"), str) else "user",
        )


def _clamp01(v: Any) -> float:
    f = float(v)
    return max(0.0, min(1.0, f))


# ---------- P-1 用户思维框架提取 ----------

# 强信号：用户明确定义/价值判断/因果模型
FRAMEWORK_PATTERNS = (
    r"我认为(?P<content>[^。！？!?，,]{4,})",
    r"我(?:还是|又|也|始终)?觉得(?P<content>[^。！？!?，,]{4,})",
    r"对我来说[，,]?(?P<content>[^。！？!?]{4,})",
    r"对我而言[，,]?(?P<content>[^。！？!?]{4,})",
    r"(?:X|一件事|东西)的本质是(?P<content>[^。！？!?]{4,})",
    r"本质是(?P<content>[^。！？!?]{4,})",
    r"与其说[^，,]{1,10}[，,]不如说(?P<content>[^。！？!?]{4,})",
    r"我一直认为(?P<content>[^。！？!?，,]{4,})",
    r"我一直觉得(?P<content>[^。！？!?，,]{4,})",
    r"我的理解是[，,](?P<content>[^。！？!?]{4,})",
    r"我始终认为(?P<content>[^。！？!?，,]{4,})",
)

# 拒绝信号：引用他人/引用块/URL/代码/反问
_REJECT_SUBSTRINGS = (
    "他说", "她说", "书上", "文章里", "某本书", "视频里", "别人",
    "https://", "http://", "```", "git ", "pip ", "npm ", "python ",
)
_QUOTE_PATTERNS = (
    re.compile(r"[「『\"“](?:与其说|我认为|我觉得)[^」』\"”]{2,}[」』\"”]"),  # 引用块
    re.compile(r"(?:的|了)?观点是|认为[^，。]{0,6}说"),                      # 转述
)
_RHETORICAL_PATTERNS = (r"你觉得呢", r"对吧[？?]*$", r"是不是[？?]*$", r"你说呢", r"懂吧", r"对吗[？?]*$")


def extract_framework_candidates(text: str, message_id: int) -> list[PersonaCandidate]:
    """P-1：从用户消息提取 user_framework 候选。

    - 强信号短语命中 → 候选（单条自我定义即可 candidate）
    - 引用他人/引用块/URL/代码/反问 → 拒绝
    - 普通事实（我喜欢X）不命中 → 空列表
    """
    if not text or not isinstance(text, str):
        return []
    t = text.strip()
    if len(t) < 4:
        return []
    low = t.lower()
    if any(s in low for s in _REJECT_SUBSTRINGS):
        return []
    if any(q.search(t) for q in _QUOTE_PATTERNS):
        return []
    if any(re.search(p, t) for p in _RHETORICAL_PATTERNS):
        return []
    persistent = any(word in t for word in ("以后", "今后", "接下来", "一直", "每次", "都"))
    if persistent and any(word in t for word in ("简短", "一句话", "只说结论", "不要详细", "别展开")):
        return [PersonaCandidate(
            kind="interaction_rule",
            title="回复长度偏好",
            content="用户明确要求：以后回复保持简短，只说结论。",
            evidence_message_ids=[message_id],
            confidence=0.95,
            stability=0.9,
            importance=0.8,
            user_confirmed=True,
            needs_confirmation=False,
        )]
    if persistent and any(word in t for word in ("详细回答", "详细说明", "展开说", "完整说明")):
        return [PersonaCandidate(
            kind="interaction_rule",
            title="回复长度偏好",
            content="用户明确要求：以后回复可以详细展开。",
            evidence_message_ids=[message_id],
            confidence=0.95,
            stability=0.9,
            importance=0.8,
            user_confirmed=True,
            needs_confirmation=False,
        )]
    cands: list[PersonaCandidate] = []
    seen: set[str] = set()
    for pat in FRAMEWORK_PATTERNS:
        m = re.search(pat, t)
        if not m:
            continue
        content = m.group("content").strip()
        if not content or content in seen:
            continue
        # 以"不是/不"开头的否定式保留原样（用户表达边界）
        seen.add(content)
        cands.append(PersonaCandidate(
            kind="user_framework",
            title=content[:20],
            content=f"用户认为：{content}",
            evidence_message_ids=[message_id],
            confidence=0.60,
            stability=0.50,
            importance=0.60,
            needs_confirmation=True,
        ))
        # 每条消息最多 2 个框架，防同一句多模式重复
        if len(cands) >= 2:
            break
    if cands:
        logger.info("persona: 提取 %d 个 user_framework 候选 (msg=%d)", len(cands), message_id)
    return cands


def validate_persona_candidate(candidate: PersonaCandidate, card) -> list[str]:
    """P-1：候选程序校验（不依赖 LLM）。

    返回问题列表，空列表 = 通过。
    """
    issues: list[str] = []
    if candidate.kind not in PERSONA_KINDS:
        issues.append(f"kind 不在白名单: {candidate.kind!r}")
    if not candidate.content.strip():
        issues.append("content 为空")
    elif len(candidate.content) > 500:
        issues.append("content 超过 500 字")
    if not candidate.evidence_message_ids:
        issues.append("evidence_message_ids 为空（空证据不得进入 active）")
    for key, val in (("confidence", candidate.confidence), ("stability", candidate.stability),
                     ("importance", candidate.importance), ("emotional_weight", candidate.emotional_weight)):
        if not 0.0 <= float(val) <= 1.0:
            issues.append(f"{key} 超出 0-1: {val!r}")
    # 敏感性：内容含密钥/验证码等直接拒绝（与 MEMORY_SPEC 5.6 一致）
    import re as _re
    if _re.search(r"(?:password|api[_-]?key|token|验证码|支付密码|银行卡|私钥)", candidate.content, _re.I):
        issues.append("content 疑似敏感信息")
    return issues


# ---------- P-2 共同意义 ----------

# 用户对过去共同事件给出解释的模式
_SHARED_MEANING_PATTERN = re.compile(
    r"(?:上次|刚才|之前|那天|那次|当时)[^。！？!?]{0,24}"
    r"(?:我们|一起|咱俩)[^。！？!?]{0,24}"
    r"(?:我觉得|我的理解是|对我来说|现在想想|仔细想想)[^。！？!?]{4,}"
)


def build_shared_meaning_candidate(
    event_summary: str,
    user_interpretation: str,
    character_interpretation: str,
    evidence_ids: list[int],
    user_confirmed: bool = False,
) -> PersonaCandidate | None:
    """P-2：事件 + 双方解释 → shared_meaning 候选。

    - 缺事件证据 → None（拒绝）
    - 缺任一方解释 → needs_confirmation=True（不伪造共识）
    - agreed_meaning 只在 user_confirmed=True（用户确认）时由用户解释代表共识；
      分歧保留在内容中，不强行合并
    """
    event_summary = (event_summary or "").strip()
    if not event_summary or not evidence_ids:
        logger.warning("persona: shared_meaning 拒绝（缺事件或证据）")
        return None
    confirmed = bool(user_confirmed)
    content = f"共同事件：{event_summary}。"
    if user_interpretation.strip():
        content += f"用户解释：{user_interpretation.strip()}。"
    if character_interpretation.strip():
        content += f"角色解释：{character_interpretation.strip()}。"
    content = content.rstrip("。") + "。"
    return PersonaCandidate(
        kind="shared_meaning",
        title=event_summary[:20],
        content=content,
        evidence_message_ids=list(evidence_ids),
        confidence=0.65,
        stability=0.5,
        importance=0.65,
        emotional_weight=0.6,
        user_confirmed=confirmed,
        needs_confirmation=not confirmed,
    )


def extract_shared_meaning_candidates(text: str, message_id: int) -> list[PersonaCandidate]:
    """P-2：用户对过去共同事件给出解释 → shared_meaning 候选。

    要求同时出现：过去事件标记（上次/刚才/那天…）+ 共同主体（我们/一起）+ 解释（我觉得/我的理解是…）。
    角色解释缺省 → needs_confirmation 候选。
    """
    if not text or len(text.strip()) < 6:
        return []
    m = _SHARED_MEANING_PATTERN.search(text)
    if not m:
        return []
    # 事件名：过去标记之后、解释之前的片段
    past = re.search(r"(上次|刚才|之前|那天|那次|当时)([^。！？!?]{2,24})", text)
    event = past.group(2).strip() if past else text[:20]
    interp = re.search(r"(?:我觉得|我的理解是|对我来说|现在想想|仔细想想)([^。！？!?]{2,})", text)
    user_interp = interp.group(1).strip() if interp else ""
    return [
        build_shared_meaning_candidate(
            event_summary=event,
            user_interpretation=user_interp,
            character_interpretation="",  # 角色解释由 P-5 反思/后续轮生成
            evidence_ids=[message_id],
            user_confirmed=False,
        )
    ]


# ---------- P-3 关系模型与 PAD ----------

# 单维变化上限（PERSONA_LOOP_SPEC 7.3/16 P-3）
REL_NORMAL_DELTA = 0.05
REL_MAJOR_DELTA = 0.12

# 事件类型 → 维度影响（正值=上升，负值=下降；major=重大事件允许 0.12）
RELATIONSHIP_EVENTS: dict[str, dict] = {
    "user_confirm":       {"trust": +0.05, "familiarity": +0.05, "cause_desc": "用户明确确认理解"},
    "shared_project_done": {"reciprocity": +0.05, "familiarity": +0.05, "cause_desc": "共同完成长期任务"},
    "boundary_violation": {"safety": -0.05, "conflict_tension": +0.05, "cause_desc": "角色越界"},
    "user_violation":     {"safety": -0.03, "conflict_tension": +0.05, "cause_desc": "用户越界"},
    "conflict_repaired":  {"trust": +0.05, "repair_progress": +0.08, "conflict_tension": -0.08, "cause_desc": "冲突修复"},
    "requested_space":    {"intimacy": 0.0, "cause_desc": "用户要求空间（主动频率下降，亲密不降）"},
    "long_absence":       {"familiarity": -0.02, "cause_desc": "长期无互动（不清零）"},
}

# 关系阶段阈值（PERSONA_LOOP_SPEC 3.4：多维派生，非 attachment 单值）
# 从最高阶段向下匹配"达到该阶段的最小必要条件"
STAGE_THRESHOLDS = (
    ("长期共同体", lambda m: m.intimacy >= 0.85 and m.safety >= 0.85 and m.reciprocity >= 0.8 and m.trust >= 0.9),
    ("亲密伙伴",   lambda m: m.intimacy >= 0.8 and m.safety >= 0.8 and m.reciprocity >= 0.7 and m.trust >= 0.8),
    ("信任",       lambda m: m.trust >= 0.72 and m.familiarity >= 0.7 and m.safety >= 0.7),
    ("熟悉",       lambda m: m.trust >= 0.55 and m.familiarity >= 0.55),
)


@dataclass
class RelationshipModel:
    """P-3：多维关系状态（attachment 保留为兼容汇总，不再单独驱动阶段）。"""

    trust: float = 0.5
    familiarity: float = 0.5
    intimacy: float = 0.5
    reciprocity: float = 0.5
    safety: float = 0.5
    conflict_tension: float = 0.2
    repair_progress: float = 0.0
    shared_projects: list[str] = field(default_factory=list)
    recurring_rituals: list[str] = field(default_factory=list)
    open_relational_threads: list[str] = field(default_factory=list)
    last_meaningful_event_id: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        import datetime
        return {
            "trust": round(self.trust, 4), "familiarity": round(self.familiarity, 4),
            "intimacy": round(self.intimacy, 4), "reciprocity": round(self.reciprocity, 4),
            "safety": round(self.safety, 4), "conflict_tension": round(self.conflict_tension, 4),
            "repair_progress": round(self.repair_progress, 4),
            "shared_projects": list(self.shared_projects),
            "recurring_rituals": list(self.recurring_rituals),
            "open_relational_threads": list(self.open_relational_threads),
            "last_meaningful_event_id": self.last_meaningful_event_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationshipModel":
        if not data:
            return cls()
        return cls(
            trust=max(0.0, min(1.0, float(data.get("trust", 0.5)))),
            familiarity=max(0.0, min(1.0, float(data.get("familiarity", 0.5)))),
            intimacy=max(0.0, min(1.0, float(data.get("intimacy", 0.5)))),
            reciprocity=max(0.0, min(1.0, float(data.get("reciprocity", 0.5)))),
            safety=max(0.0, min(1.0, float(data.get("safety", 0.5)))),
            conflict_tension=max(0.0, min(1.0, float(data.get("conflict_tension", 0.2)))),
            repair_progress=max(0.0, min(1.0, float(data.get("repair_progress", 0.0)))),
            shared_projects=list(data.get("shared_projects") or []),
            recurring_rituals=list(data.get("recurring_rituals") or []),
            open_relational_threads=list(data.get("open_relational_threads") or []),
            last_meaningful_event_id=str(data.get("last_meaningful_event_id") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    @classmethod
    def from_initial(cls, initial_affection: float = 0.5) -> "RelationshipModel":
        """P-3：initial_affection 只做 intimacy/familiarity 先验，不自动 trust/safety。"""
        ia = max(0.0, min(1.0, float(initial_affection)))
        return cls(intimacy=ia, familiarity=ia)


def apply_relationship_event(model: RelationshipModel, event: dict) -> RelationshipModel:
    """P-3：确定性关系事件更新（带 event_id 去重、单维变化上限、cause 必填）。

    event: {"type": str, "cause": str, "event_id": str | None, "delta": {dim: ±float} | None}
    """
    import datetime
    etype = event.get("type", "")
    if etype not in RELATIONSHIP_EVENTS and not event.get("delta"):
        logger.warning("relationship: 未知事件类型 %r（忽略）", etype)
        return model
    eid = event.get("event_id")
    if eid and eid == model.last_meaningful_event_id:
        return model  # 幂等：同事件不重放
    delta = dict(event.get("delta") or {})
    for dim, d in (RELATIONSHIP_EVENTS.get(etype) or {}).items():
        if dim != "cause_desc":
            delta.setdefault(dim, d)
    # 重大事件（major_event / delta 显式提供）允许 0.12，其余 0.05
    limit = REL_MAJOR_DELTA if (etype == "major_event" or event.get("delta")) else REL_NORMAL_DELTA
    out = model.to_dict()
    for dim, d in delta.items():
        if dim not in ("trust", "familiarity", "intimacy", "reciprocity", "safety",
                       "conflict_tension", "repair_progress"):
            continue
        step = max(-limit, min(limit, float(d)))
        out[dim] = max(0.0, min(1.0, getattr(model, dim) + step))
    if etype == "shared_project_done":
        cause = event.get("cause", "")
        if cause and cause not in out["shared_projects"]:
            out["shared_projects"] = out["shared_projects"] + [cause]
    if eid:
        out["last_meaningful_event_id"] = eid
    out["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    logger.info("relationship event: %s (%s)", etype, event.get("cause", ""))
    return RelationshipModel.from_dict(out)


def derive_relationship_stage(model: RelationshipModel) -> str:
    """P-3：多维状态 → 关系阶段（STAGE_THRESHOLDS 从高到低，首个满足=最高阶段）。"""
    for stage, cond in STAGE_THRESHOLDS:
        if cond(model):
            return stage
    return "初识"


def apply_emotion_event(state, event: dict) -> None:
    """P-3：PAD 情绪事件（增量钳制 0.2/次，衰减向 0.5 基线回归）。

    state 需有 valence/arousal/dominance/last_cause 属性（AgentState P-3 字段）。
    event: {"type", "cause", "delta": {valence/arousal/dominance: ±float}}
    """
    delta = event.get("delta") or {}
    for dim in ("valence", "arousal", "dominance"):
        d = delta.get(dim)
        if d is None:
            continue
        old = getattr(state, dim, 0.5)
        step = max(-0.2, min(0.2, float(d)))
        setattr(state, dim, max(0.0, min(1.0, old + step)))
    if hasattr(state, "last_cause"):
        state.last_cause = event.get("cause") or event.get("type", "emotion")
    # decay：无 delta 时向基线回归 10%
    if not delta:
        for dim in ("valence", "arousal", "dominance"):
            old = getattr(state, dim, 0.5)
            setattr(state, dim, old + (0.5 - old) * 0.1)


# ---------- P-4 Persona Brief ----------

# 每类最多条数与总条数/字符上限（PERSONA_LOOP_SPEC 8）
PERSONA_BRIEF_PER_CATEGORY = 2
PERSONA_BRIEF_MAX_ITEMS = 6
PERSONA_BRIEF_MAX_CHARS = 1800


@dataclass
class PersonaBrief:
    """P-4：人格上下文（如何理解和回应），与 Memory Brief（有哪些证据）分离。"""

    core_tensions: list[str] = field(default_factory=list)
    relevant_user_frameworks: list[dict] = field(default_factory=list)
    relevant_character_beliefs: list[dict] = field(default_factory=list)
    shared_meanings: list[dict] = field(default_factory=list)
    relationship_context: dict = field(default_factory=dict)
    inner_state: dict = field(default_factory=dict)
    open_tensions: list[str] = field(default_factory=list)


def _query_hits(query: str, *fields: str) -> bool:
    """轻量相关性：query 与字段出现 2+ 字重叠词即相关（低成本近似，无 embedding）。"""
    q = query or ""
    if not q:
        return False
    qchars = set(q)
    for f in fields:
        f = f or ""
        if not f:
            continue
        # 2-4 字滑动窗口重叠
        for n in (3, 2):
            for i in range(len(q) - n + 1):
                if q[i:i + n] in f:
                    return True
    return False


def build_persona_brief(
    query: str,
    card,
    relationship,
    state,
    memory,
    *,
    max_chars: int = PERSONA_BRIEF_MAX_CHARS,
) -> PersonaBrief:
    """P-4：按相关性选人格上下文，每类 ≤2 条、总计 ≤6 条、≤max_chars。"""
    cp = card.core_profile
    tensions = [f"{t['left']} / {t['right']}" for t in cp.get("inner_tensions", [])]
    brief = PersonaBrief(core_tensions=tensions)

    def _pick(layer: str, kind: str) -> list[dict]:
        out = []
        for e in memory.list_layer(layer, limit=30):
            meta = e.meta or {}
            if meta.get("kind") != kind:
                continue
            scope = meta.get("scope") or []
            title = meta.get("title") or ""
            if _query_hits(query, e.content, title, *([s if isinstance(s, str) else "" for s in scope])):
                out.append({"content": e.content, "kind": kind})
            if len(out) >= PERSONA_BRIEF_PER_CATEGORY:
                break
        return out

    brief.relevant_user_frameworks = _pick("semantic", "user_framework")
    brief.relevant_character_beliefs = _pick("semantic", "character_belief")
    brief.shared_meanings = _pick("episodic", "shared_meaning")

    # 关系上下文（不暴露内部数值，只给阶段与边界）
    stage = derive_relationship_stage(relationship)
    brief.relationship_context = {
        "stage": stage,
        "trusted": relationship.trust >= 0.7,
        "conflict": relationship.conflict_tension > 0.5,
    }
    # 内在状态：PAD 文字化（低/中/高），不暴露数值
    v, a, d = getattr(state, "valence", 0.5), getattr(state, "arousal", 0.5), getattr(state, "dominance", 0.5)
    brief.inner_state = {
        "valence": "愉悦" if v >= 0.65 else ("低落" if v <= 0.35 else "平稳"),
        "arousal": "兴奋" if a >= 0.65 else ("平静" if a <= 0.35 else "中等"),
        "dominance": "掌控" if d >= 0.65 else ("被动" if d <= 0.35 else "均衡"),
    }
    # 总条数限制
    all_items = (brief.relevant_user_frameworks + brief.relevant_character_beliefs + brief.shared_meanings)
    if len(all_items) > PERSONA_BRIEF_MAX_ITEMS:
        brief.relevant_user_frameworks = brief.relevant_user_frameworks[:PERSONA_BRIEF_MAX_ITEMS]
        brief.relevant_character_beliefs = []
        brief.shared_meanings = []
    # 字符预算（完整条目截断，不在句中硬切）
    total = len(format_persona_brief(brief))
    if total > max_chars:
        brief.shared_meanings = []
        brief.relevant_character_beliefs = []
        while brief.relevant_user_frameworks and len(format_persona_brief(brief)) > max_chars:
            brief.relevant_user_frameworks = brief.relevant_user_frameworks[:-1]
    return brief


def format_persona_brief(brief: PersonaBrief) -> str:
    """P-4：注入文本（不暴露 memory id/置信度数值/stability）。"""
    parts: list[str] = []
    if brief.core_tensions:
        parts.append("【内在张力】" + "；".join(brief.core_tensions))
    if brief.relationship_context:
        rc = brief.relationship_context
        parts.append(f"【关系】你们处于{rc['stage']}阶段。"
                     + ("她信任你，可以表达分歧。" if rc["trusted"] else "她还在建立信任，注意分寸。")
                     + ("当前关系有未消解的压力。" if rc["conflict"] else ""))
    if brief.inner_state:
        ins = brief.inner_state
        parts.append(f"【当下状态】情绪{ins['valence']}、状态{ins['arousal']}、{ins['dominance']}感。")
    if brief.relevant_user_frameworks:
        lines = "\n".join(f"- {f['content']}" for f in brief.relevant_user_frameworks)
        parts.append(
            f"【理解用户】用户表达过以下观点/框架（回用时执行扩展、对照、限定适用边界或应用到当前问题，"
            f"不要逐字复述用户原句，不要每轮都提起）：\n{lines}"
        )
    if brief.relevant_character_beliefs:
        lines = "\n".join(f"- {f['content']}" for f in brief.relevant_character_beliefs)
        parts.append(f"【角色观点】你形成过的观点：\n{lines}")
    if brief.shared_meanings:
        lines = "\n".join(f"- {f['content']}" for f in brief.shared_meanings)
        parts.append(f"【共同意义】你们对某些共同经历的解释：\n{lines}")
    return "\n".join(parts)


# ---------- P-6 回用与防回声室 ----------

REUSE_ACTIONS = ("extend", "contrast", "question", "apply", "remember", "none")
REUSE_COOLDOWN_TURNS = 8  # 同一框架显式引用冷却（PERSONA_LOOP_SPEC 9.2）


class ReuseCooldown:
    """P-6：框架引用冷却（frame_id → 上次引用轮次，满 N 轮恢复）。"""

    def __init__(self, turns: int = REUSE_COOLDOWN_TURNS):
        self.turns = turns
        self._last_turn: dict[str, int] = {}

    def allow(self, frame_id: str, turn: int) -> bool:
        last = self._last_turn.get(frame_id)
        if last is None or turn - last >= self.turns:
            self._last_turn[frame_id] = turn
            return True
        return False


def choose_reuse_action(brief: PersonaBrief, query: str, state) -> str:
    """P-6：从注入的人格上下文中选回用动作（默认 apply，绝不为 repeat）。

    - 无相关框架/共同意义 → none
    - 低愉悦（valence<0.4）→ question（先确认边界，不直接断言）
    - 关系冲突（conflict_tension>0.5）→ contrast（保留分歧）
    - 用户直接回溯共同事件 → remember（"你还记得……"类）
    - 其余 → apply（用于当前具体问题）
    """
    has_fw = bool(brief.relevant_user_frameworks) or bool(brief.relevant_character_beliefs)
    has_sm = bool(brief.shared_meanings)
    if not has_fw and not has_sm:
        return "none"
    if has_sm and query and any(k in query for k in ("记得", "那晚", "上次", "那次", "当时")):
        return "remember"
    if has_fw and not has_sm:
        valence = float(getattr(state, "valence", 0.5))
        conflict = float(getattr(state, "conflict_tension", 0.0))
        if conflict > 0.5:
            return "contrast"
        if valence < 0.4:
            return "question"
    return "apply"


# ---------- P-7 关系冲突闭环 ----------

CONFLICT_STATES = ("open", "acknowledged", "clarifying", "repairing", "closed", "boundary_held")


class ConflictTracker:
    """P-7：关系冲突状态机（open→acknowledged→clarifying→repairing→closed / boundary_held）。

    用户澄清只推进状态，不自动清零余波（需显式 repair）；关闭后不持续惩罚。
    """

    def __init__(self):
        self._conflicts: dict[str, dict] = {}

    def open(self, conflict_id: str, *, cause: str, evidence_ids: list[int] | None = None) -> None:
        self._conflicts[conflict_id] = {
            "id": conflict_id, "status": "open", "cause": cause,
            "evidence_ids": list(evidence_ids or []),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        logger.info("conflict open: %s (%s)", conflict_id, cause)

    def _transition(self, conflict_id: str, status: str) -> None:
        c = self._conflicts.get(conflict_id)
        if c is None:
            logger.warning("conflict %s 不存在，忽略 %s", conflict_id, status)
            return
        c["status"] = status
        c["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def acknowledge(self, conflict_id: str) -> None:
        self._transition(conflict_id, "acknowledged")

    def clarify(self, conflict_id: str) -> None:
        c = self._conflicts.get(conflict_id)
        if c is None:
            return
        c["clarify_count"] = int(c.get("clarify_count", 0)) + 1
        self._transition(conflict_id, "clarifying")

    def repair(self, conflict_id: str) -> None:
        self._transition(conflict_id, "repairing")

    def close(self, conflict_id: str) -> None:
        self._transition(conflict_id, "closed")

    def hold_boundary(self, conflict_id: str) -> None:
        self._transition(conflict_id, "boundary_held")

    def status(self, conflict_id: str) -> str | None:
        c = self._conflicts.get(conflict_id)
        return c["status"] if c else None

    def open_conflicts(self) -> list[dict]:
        """未闭合冲突（boundary_held 视为未解决张力，仍列入）。"""
        return [c for c in self._conflicts.values()
                if c["status"] not in ("closed",)]

    def to_dict(self) -> dict:
        return dict(self._conflicts)

    @classmethod
    def from_dict(cls, data: dict) -> "ConflictTracker":
        t = cls()
        for cid, c in (data or {}).items():
            if isinstance(c, dict) and c.get("status") in CONFLICT_STATES:
                t._conflicts[cid] = dict(c)
        return t


# 用户澄清/道歉模式（推进冲突）与越界反馈模式（新开冲突）
APOLOGY_PATTERNS = ("对不起", "抱歉", "不是那个意思", "开玩笑的", "别生气", "我错了", "误会")
VIOLATION_PATTERNS = ("你太过分", "你越界", "你烦不烦", "你让我不舒服", "别这样", "停一下")


def note_conflict_from_user_text(tracker: ConflictTracker, text: str) -> str | None:
    """P-7：从用户消息检测冲突信号。

    - 道歉/澄清 → 把所有 open 冲突推进到 clarifying（不自动 closed）
    - 越界反馈 → 新开冲突（id 用时间戳）
    返回触发的动作（"clarify"/"violation"/None）。
    """
    if not text:
        return None
    if any(p in text for p in APOLOGY_PATTERNS):
        for c in tracker.open_conflicts():
            if c["status"] in ("open", "acknowledged"):
                tracker.clarify(c["id"])
        return "clarify"
    if any(p in text for p in VIOLATION_PATTERNS):
        import time as _time
        cid = f"violation-{int(_time.time() * 1000)}"
        tracker.open(cid, cause=text[:40], evidence_ids=[])
        return "violation"
    return None


# ---------- P-9 表达控制面 ----------

RESPONSE_PLAN_MAX_CHARS = 400  # 结构化计划上限（不保存自由思维链）


@dataclass(frozen=True)
class ResponsePlan:
    """P-9：结构化表达计划（intent/开场/要点/不确定性），不暴露私密 CoT。"""

    intent: str
    confidence: float
    recalled_frame_ids: tuple[str, ...] = ()
    conflict: str = ""
    opening_move: str = ""
    key_point: str = ""
    uncertainty_to_express: str = ""
    desired_length: str = "normal"
    association_target: str = ""


def build_response_plan(context: dict, brief: PersonaBrief, state) -> ResponsePlan | None:
    """P-9：复杂回复生成计划；简单事实问答返回 None（跳过）。

    触发：未闭合冲突（conflict_tension>0.5）、相关框架注入、共同意义被回溯、低愉悦。
    联想（association_target）只来自注入的共同意义（可追溯），禁止无因随机跑题。
    """
    conflict = float(getattr(state, "conflict_tension", 0.0))
    valence = float(getattr(state, "valence", 0.5))
    q = str(context.get("user_text", ""))
    has_fw = bool(brief.relevant_user_frameworks) or bool(brief.relevant_character_beliefs)
    has_sm = bool(brief.shared_meanings)
    explicit_style_length = str(context.get("explicit_style_length") or "")
    if explicit_style_length not in {"short", "long"}:
        explicit_style_length = ""
    requested_length = ""
    if any(word in q for word in ("不要详细", "不用详细", "无需详细", "无需展开", "不用展开", "简单说", "简短", "一句话", "只说结论", "别展开")):
        requested_length = "short"
    elif any(word in q for word in ("详细", "展开说", "具体说明", "完整说明")):
        requested_length = "long"
    if not has_fw and not has_sm and conflict <= 0.5 and valence >= 0.35 and not requested_length and not explicit_style_length:
        return None  # 简单轮：跳过计划
    if conflict > 0.5:
        intent, opening = "clarify", "先确认边界"
    elif valence < 0.35:
        intent, opening = "comfort", "先接住情绪"
    elif has_sm and any(k in q for k in ("记得", "那晚", "上次", "那次")):
        intent, opening = "reflect", "从共同记忆切入"
    elif has_fw:
        intent, opening = "apply", "先回应再展开"
    else:
        intent, opening = "answer", "直接回应"
    frame_ids = tuple(f["content"][:12] for f in brief.relevant_user_frameworks[:2])
    target = ""
    if has_sm:
        target = brief.shared_meanings[0]["content"][:30]
    style_length = str(context.get("style_length") or "normal")
    if style_length not in {"short", "normal", "long"}:
        style_length = "normal"
    if requested_length:
        desired_length = requested_length
    elif explicit_style_length:
        desired_length = explicit_style_length
    elif valence < 0.3:
        desired_length = "short"
    else:
        desired_length = style_length
    return ResponsePlan(
        intent=intent,
        confidence=0.6,
        recalled_frame_ids=frame_ids,
        conflict="" if conflict <= 0.5 else "有未消解关系压力",
        opening_move=opening,
        key_point="",
        uncertainty_to_express="",
        desired_length=desired_length,
        association_target=target,
    )


def render_authenticity(text: str, authenticity: dict, channel: str) -> dict:
    """P-9：PAD → 表达风格提示（不重写事实；文本一致性由 prompt 层保证）。

    返回 {"text": 原文, "style_hint": short/normal/long, "energy": 0-1, "tts_short": bool}。
    同一输入+状态 → 同一输出（无随机反常）。
    """
    valence = float(authenticity.get("valence", 0.5))
    arousal = float(authenticity.get("arousal", 0.5))
    if valence <= 0.35 and arousal <= 0.35:
        hint = "short"
    elif arousal >= 0.7:
        hint = "short"  # 高唤醒：短句爆发
    elif valence >= 0.65 and arousal < 0.5:
        hint = "normal"
    else:
        hint = "normal"
    return {
        "text": text,
        "style_hint": hint,
        "energy": max(0.0, min(1.0, (valence + arousal) / 2)),
        "tts_short": channel == "tts" and hint == "short",
    }


# ---------- Persona Imprint（表层人格印记） ----------

IMPRINT_ACTIVE_THRESHOLD = 3  # 同方向跨场景反馈次数


class ImprintTracker:
    """P-9：表层人格印记（candidate → active/rejected）。

    单次反馈只形成 candidate；同方向跨场景达到阈值才 active；负反馈直接 rejected。
    印记只调表层行为倾向，不修改 Character Core。
    """

    def __init__(self):
        self._imprints: dict[str, dict] = {}

    def note(self, dimension: str, direction: float, evidence: int, scope: str = "") -> None:
        cur = self._imprints.setdefault(dimension, {
            "dimension": dimension, "direction": 0.0, "count": 0,
            "evidence_ids": [], "scope": scope, "status": "candidate",
        })
        if cur["status"] == "rejected":
            return
        if direction < 0:
            cur["status"] = "rejected"
            cur["evidence_ids"] = list(cur["evidence_ids"]) + [evidence]
            return
        cur["direction"] = max(cur["direction"], float(direction))
        cur["count"] = cur["count"] + 1
        cur["evidence_ids"] = list(cur["evidence_ids"]) + [evidence]
        if cur["scope"] and scope and cur["scope"] != scope:
            cur["scope"] = "跨场景"  # 跨场景证据 → 更稳定
        if cur["count"] >= IMPRINT_ACTIVE_THRESHOLD:
            cur["status"] = "active"

    def status(self, dimension: str) -> str | None:
        im = self._imprints.get(dimension)
        return im["status"] if im else None

    def active_imprints(self) -> list[tuple[str, str]]:
        return [(d, im.get("scope", "")) for d, im in self._imprints.items() if im["status"] == "active"]

    def to_dict(self) -> dict:
        return {d: dict(im) for d, im in self._imprints.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "ImprintTracker":
        t = cls()
        for d, im in (data or {}).items():
            if isinstance(im, dict) and im.get("status") in ("candidate", "active", "rejected"):
                t._imprints[d] = dict(im)
        return t


# ---------- PersonaCandidate → MemoryCandidate 转换 ----------

def persona_candidate_to_memory(candidate: PersonaCandidate, source_message_id: int) -> dict | None:
    """P-1：窄转换（PERSONA_LOOP_SPEC 15 映射表）。

    转换失败返回 None 并记日志，不阻断回复。
    """
    kind = candidate.kind
    if kind not in KIND_DEFAULT_CONFIDENCE:
        logger.warning("persona_candidate_rejected: unknown kind %s", kind)
        return None
    layer = {
        "user_framework": "semantic",
        "character_belief": "semantic",
        "shared_meaning": "episodic",
        "relationship_event": "episodic",
        "interaction_rule": "procedural",
    }[kind]
    meta = {
        "kind": kind,
        "title": candidate.title,
        "scope": list(candidate.scope),
        "stability": candidate.stability,
        "emotional_weight": candidate.emotional_weight,
        "user_confirmed": candidate.user_confirmed,
        "role_compatible": candidate.role_compatible,
        "needs_confirmation": candidate.needs_confirmation,
        "evidence_message_ids": list(candidate.evidence_message_ids),
    }
    return {
        "kind": kind,
        "layer": layer,
        "content": candidate.content,
        "confidence": KIND_DEFAULT_CONFIDENCE[kind],
        "importance": candidate.importance,
        "source": "rule_extract",
        "source_message_id": source_message_id,
        "subject": candidate.subject,
        "meta": meta,
        "needs_confirmation": candidate.needs_confirmation,
    }

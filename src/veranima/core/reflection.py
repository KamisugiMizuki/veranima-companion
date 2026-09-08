"""P-5 反思整合（PERSONA_LOOP_SPEC P-5）：低频触发 → 候选 → 程序校验 → 局部应用。

本模块只产结构化更新并写回 models dict / MemoryStore；不直接发送消息，
不修改 Character Core，不每轮自省。
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 触发白名单（PERSONA_LOOP_SPEC 7.1）
REFLEX_TRIGGERS = {
    "high_emotion_event", "user_correction", "conflict_repaired", "persona_candidates_20",
}

# 稳定特征：禁止自动修改（PERSONA_LOOP_SPEC 7.3）
PROTECTED_SELF_FIELDS = ("stable_traits", "identity_summary")

# P-5 LLM 反思 prompt（角色第一人称；禁止「用户」二字）
_REFLECT_PROMPT = """下面是你最近记下的几条东西，关于他，或者关于你们之间。

{ev}

用你自己的视角写两句话：你注意到他、或者你们之间有什么变化；你因此明白了什么。
只输出 JSON：{{"observed_change": "你注意到的变化", "self_belief": "用「我」开头，你因此明白的", "confidence": 0.0-1.0}}
只写你从上面真能看出来的，不要编。每条不超过 40 字，不要出现「用户」二字。"""


@dataclass
class PersonaReflection:
    """P-5：结构化反思候选（proposed/validated/applied/rejected）。"""

    evidence_ids: list[int]
    observed_change: str
    self_model_update: dict = field(default_factory=dict)
    relationship_update: dict = field(default_factory=dict)
    user_model_update: dict = field(default_factory=dict)
    unresolved_tension: str = ""
    confidence: float = 0.5
    proposed_at: str = ""
    status: str = "proposed"

    def to_dict(self) -> dict:
        return {
            "evidence_ids": list(self.evidence_ids),
            "observed_change": self.observed_change,
            "self_model_update": dict(self.self_model_update),
            "relationship_update": dict(self.relationship_update),
            "user_model_update": dict(self.user_model_update),
            "unresolved_tension": self.unresolved_tension,
            "confidence": round(float(self.confidence), 4),
            "proposed_at": self.proposed_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PersonaReflection":
        return cls(
            evidence_ids=[int(x) for x in d.get("evidence_ids", [])],
            observed_change=str(d.get("observed_change", "")),
            self_model_update=dict(d.get("self_model_update") or {}),
            relationship_update=dict(d.get("relationship_update") or {}),
            user_model_update=dict(d.get("user_model_update") or {}),
            unresolved_tension=str(d.get("unresolved_tension") or ""),
            confidence=float(d.get("confidence", 0.5)),
            proposed_at=str(d.get("proposed_at") or ""),
            status=str(d.get("status", "proposed")),
        )


def reflection_due(trigger: str, counters: dict) -> bool:
    """P-5：触发判定（普通消息/随机 tick 不触发）。

    - user_correction / high_emotion_event / conflict_repaired：即时触发
    - persona_candidates_20：计数器 ≥20 时触发
    """
    if trigger not in REFLEX_TRIGGERS:
        return False
    if trigger == "persona_candidates_20":
        return int(counters.get("persona_candidates", 0)) >= 20
    return True


def propose_reflection(evidence: list[dict]) -> PersonaReflection | None:
    """P-5：从证据生成结构化反思候选（纯规则版；LLM 增强可选，默认关闭）。

    evidence: [{id, kind, content, confidence, ...}]（来自 memory entries）
    只处理 shared_meaning / user_framework / relationship_event 证据。
    """
    if not evidence:
        return None
    ids = [int(e["id"]) for e in evidence if e.get("id") is not None]
    if not ids:
        return None
    # 单字段局部更新：取最重要的证据内容作为 learned_belief 候选
    best = max(evidence, key=lambda e: float(e.get("confidence", 0.5)))
    belief = str(best.get("content", "")).strip()
    update: dict[str, Any] = {}
    if belief:
        update["learned_beliefs"] = [belief]
    return PersonaReflection(
        evidence_ids=ids,
        observed_change=f"基于 {len(ids)} 条人格证据形成新理解",
        self_model_update=update,
        relationship_update={},
        user_model_update={},
        unresolved_tension="",
        confidence=min(1.0, 0.4 + 0.05 * len(ids)),
        proposed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        status="proposed",
    )


def propose_reflection_llm(evidence: list[dict], task_fn) -> PersonaReflection | None:
    """P-5：LLM 版反思候选（角色第一人称）。task_fn(prompt) -> str。

    规则版只是把置信度最高的记忆原文当 learned_belief——实机那几条吐出来的是
    「用户计划将后端迁移至 hermes 系统」这类用户事务，不是角色对关系的理解。
    反思要的是「我因此明白了什么」→ 交 LLM 写；失败或第三人称泄漏返回 None，
    调用方回退规则版。
    """
    if not evidence or task_fn is None:
        return None
    ids = [int(e["id"]) for e in evidence if e.get("id") is not None]
    if not ids:
        return None
    ev = "\n".join(f"{i}. {str(e.get('content', '')).strip()[:120]}"
                   for i, e in enumerate(evidence, 1))
    try:
        raw = str(task_fn(_REFLECT_PROMPT.format(ev=ev)) or "")
    except Exception as exc:  # 不阻断对话，回退规则版
        logger.warning("reflection llm failed: %s", exc)
        return None
    try:
        data = json.loads(raw.strip().strip("`").removeprefix("json").strip())
    except (json.JSONDecodeError, TypeError):
        logger.warning("reflection llm returned non-JSON; fallback to rule")
        return None
    if not isinstance(data, dict):
        return None
    belief = str(data.get("self_belief") or "").strip()[:120]
    change = str(data.get("observed_change") or "").strip()[:120]
    if not belief:
        return None
    # 第三人称泄漏防线：角色不该把用户叫「用户」（B1 同源）
    if "用户" in belief or "用户" in change:
        logger.warning("reflection llm leaked third person; fallback to rule")
        return None
    try:
        conf = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        conf = 0.6
    return PersonaReflection(
        evidence_ids=ids,
        observed_change=change or f"基于 {len(ids)} 条人格证据形成新理解",
        self_model_update={"learned_beliefs": [belief]},
        confidence=max(0.0, min(1.0, conf)),
        proposed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        status="proposed",
    )


def validate_reflection(reflection: PersonaReflection, card) -> list[str]:
    """P-5：程序校验（不依赖 LLM）。返回问题列表，空 = 通过。"""
    issues: list[str] = []
    if not reflection.evidence_ids:
        issues.append("evidence_ids 为空")
    if reflection.status not in ("proposed", "validated"):
        issues.append(f"status 非法: {reflection.status!r}")
    if not 0.0 <= float(reflection.confidence) <= 1.0:
        issues.append("confidence 超出 0-1")
    # 核心兼容：learned_belief 与角色卡禁忌/价值观冲突
    ver = (card.veranima or {}) if card is not None else {}
    taboos = ver.get("taboos") or []
    if isinstance(taboos, str):
        # 角色卡的 taboos 常是一整段（分号/换行分隔）。直接 for 会遍历**字符**，
        # 单字「不/我/你」命中一切 → 每条 belief 全判冲突（09-08 实测 24 条假
        # 冲突把反思链整条毙掉）。按分隔符切条目后再做整条包含判定。
        taboos = [s.strip() for s in taboos.replace("；", ";").replace("\n", ";").split(";")
                  if s.strip()]
    for belief in reflection.self_model_update.get("learned_beliefs", []):
        for t in taboos:
            if isinstance(t, str) and t and t in str(belief):
                issues.append(f"learned_belief 与角色禁忌冲突: {t}")
        for core_val in (ver.get("value_order") or ver.get("values") or []):
            pass  # 价值观冲突需要语义判断，规则层不误判；只拦截明确禁忌词
    # 稳定特征保护：self_model_update 不得包含受保护字段
    for f in PROTECTED_SELF_FIELDS:
        if f in reflection.self_model_update:
            issues.append(f"self_model_update 不得修改受保护字段: {f}")
    return issues


def apply_reflection(reflection: PersonaReflection, models: dict) -> dict:
    """P-5：应用反思结果（原地返回新 dict，不改调用方对象）。

    - 仅 status=validated/applied 时应用
    - 一次最多改一个局部字段（self_model_update 或 relationship_update 二选一优先 self）
    - 版本 +1，保留旧版本（由调用方存 MemoryStore 版本链）
    """
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in models.items()}
    if reflection.status not in ("validated", "applied"):
        logger.info("reflection not applied (status=%s)", reflection.status)
        return out
    sm = out.get("self_model")
    if isinstance(sm, dict) and reflection.self_model_update:
        for f in PROTECTED_SELF_FIELDS:
            reflection.self_model_update.pop(f, None)  # 防御：稳定特征不写
        applied = False
        for k, v in reflection.self_model_update.items():
            if k not in PROTECTED_SELF_FIELDS:
                sm[k] = v
                applied = True
        if applied:
            sm["version"] = int(sm.get("version", 1)) + 1
            sm["evidence_ids"] = list(reflection.evidence_ids)
            sm["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rel = out.get("relationship")
    if isinstance(rel, dict) and reflection.relationship_update and not reflection.self_model_update:
        rel.update({k: v for k, v in reflection.relationship_update.items()
                    if k in ("trust", "familiarity", "intimacy", "reciprocity", "safety",
                             "conflict_tension", "repair_progress")})
    logger.info("reflection applied: evidence=%s self=%s", reflection.evidence_ids, bool(sm))
    return out

"""C-4：共同创作 → 关系事件接线（SHARED_CREATION_SPEC 5.3）。

共同事件确认后产生至多一个「待审核」关系候选（不直接改关系值）；
用户确认后才经 apply_relationship_event 落到 RelationshipModel。
规则（SPEC §5.3 程序规则）：
- 事件必须有 evidence + 用户确认（confirm_shared_event 已保证）
- 同一项目连续事件不线性累加：同项目冷却期内的新事件降权为 0 delta 审计记录
- 消息数量/项目数量本身不加 attachment
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 同项目关系事件冷却：期间内的第二个事件只记审计、不再推动维度
COOLDOWN_PER_PROJECT = 1


def build_shared_creation_relationship_candidate(
    *,
    project_id: str,
    project_title: str,
    summary: str,
    event_id: str,
    evidence_message_ids: list[int],
    completed: bool = False,
    prior_events_in_project: int = 0,
) -> dict | None:
    """构造待审核 relationship_event 候选（PERSONA_LOOP P-3 契约形状）。"""
    if not summary.strip() or not evidence_message_ids:
        return None
    if completed:
        etype = "shared_project_done"
        content = f"我们共同完成了「{project_title}」：{summary.strip()}"
    else:
        etype = "user_confirm"
        content = f"我们在共同项目「{project_title}」中确认了一段共同经历：{summary.strip()}"
    return {
        "kind": "relationship_event",
        "event_id": f"rel-{event_id}",
        "event_type": etype,
        "content": content[:300],
        "project_id": project_id,
        "evidence_message_ids": sorted(set(int(x) for x in evidence_message_ids)),
        # 冷却降权：同项目第 N 个事件（N>1）在候选层标记，由确认方跳过维度更新
        "cooldown_active": prior_events_in_project >= COOLDOWN_PER_PROJECT,
        "prior_events_in_project": int(prior_events_in_project),
        "source": "shared_creation",
    }

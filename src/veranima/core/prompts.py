"""系统 prompt 组装：身份认知 + 角色卡 + 当前状态 + 记忆注入（预算控制）。"""

from __future__ import annotations

from .character import CharacterCard
from .state import AgentState
from ..memory.store import MemoryStore

# 层 → prompt 标签与预算（DESIGN.md：core_profile 1200 / 段落 1600 / session 600）
LAYER_LABELS = {
    "core_profile": "【常驻档案】",
    "semantic": "【关于你的长期事实】",
    "episodic": "【我们的共同回忆】",
    "procedural": "【我答应过你/你要求的规则】",
}


def build_system_prompt(
    card: CharacterCard,
    state: AgentState,
    memory: MemoryStore,
    *,
    core_profile_budget: int = 1200,
    section_budget: int = 1600,
    session_budget: int = 600,
    extra_blocks: list[str] | None = None,
) -> str:
    """按预算组装系统 prompt。记忆按层注入，超出预算截断。extra_blocks 为附加块（学习参数/镜像/承诺）。"""
    parts = [card.to_system_prompt()]
    parts.append(state.to_prompt_block())

    # core_profile + procedural：全量（预算内）
    for layer, label in (("core_profile", LAYER_LABELS["core_profile"]), ("procedural", LAYER_LABELS["procedural"])):
        entries = memory.list_layer(layer, limit=50)
        if not entries:
            continue
        texts = [e.content for e in entries]
        budget = core_profile_budget if layer == "core_profile" else section_budget
        joined = "\n".join(texts)
        if len(joined) > budget:
            joined = joined[:budget] + "…"
        parts.append(label + "\n" + joined)

    # semantic + episodic：检索注入（8.7.2 记得感分级：按 strength 措辞）
    query_hint = _latest_query(memory)
    if query_hint:
        for layer, label in (("semantic", LAYER_LABELS["semantic"]), ("episodic", LAYER_LABELS["episodic"])):
            entries = memory.recall(query_hint, top_k=5, layer=layer)
            if not entries:
                continue
            texts = [format_memory_line(e) for e in entries if e.strength >= 0.3]
            if not texts:
                continue
            joined = "\n".join(texts)
            if len(joined) > section_budget:
                joined = joined[:section_budget] + "…"
            parts.append(label + "\n" + joined)

    # session：最近会话摘要（简化：最近对话由历史列表承担，这里注入会话层记忆）
    session_entries = memory.list_layer("session", limit=10)
    if session_entries:
        joined = "\n".join(e.content for e in session_entries)
        if len(joined) > session_budget:
            joined = joined[:session_budget] + "…"
        parts.append("【本次会话】\n" + joined)

    # 附加块（MVP2：风格参数 / 语言镜像 / 承诺提醒）
    for block in (extra_blocks or []):
        if block:
            parts.append(block)

    return "\n".join(parts)


def format_memory_line(entry) -> str:
    """记忆行格式化（8.7.2）：按 strength 分级措辞 + 情感色彩。

    - strength ≥0.7：我记得你……
    - 0.4~0.7：我好像记得……
    - <0.4：我隐约记得……
    - meta.emotion 存在时附加（"你提起时听起来很开心"）
    """
    if entry.strength >= 0.7:
        verb = "我记得"
    elif entry.strength >= 0.4:
        verb = "我好像记得"
    else:
        verb = "我隐约记得"
    line = f"- {verb}：{entry.content}"
    emotion = (entry.meta or {}).get("emotion")
    if emotion:
        line += f"（你提起时听起来{emotion}）"
    return line


def _latest_query(memory: MemoryStore) -> str:
    """用最近一条用户消息作为检索查询。"""
    msgs = memory.recent_messages(limit=3)
    for m in reversed(msgs):
        if m["role"] == "user":
            return m["content"]
    return ""

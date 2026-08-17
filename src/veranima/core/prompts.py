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

# 通道语境（DESIGN 4.8 通道感知：表达风格挂通道不挂角色卡）
CHANNEL_CONTEXT = {
    "im": "【当前场景】你正在用文字聊天软件打字聊天。打字要利落，不用「嗯…那个…」这类口语填充词；"
          "长内容拆短句，标点表达情绪；不模仿语音停顿。",
    "tts": "【当前场景】你正在用户旁边说话交流。可以口语化，允许「嗯…」「那个…」填充词、"
           "自我修正和重复；像面对面聊天一样自然。",
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
    channel: str = "im",
) -> str:
    """按预算组装系统 prompt。记忆按层注入，超出预算截断。extra_blocks 为附加块（学习参数/镜像/承诺）。

    channel 注入通道语境（DESIGN 4.8 通道感知）：im=打字聊天（利落/去填充词），tts=语音（口语化/允许填充词）。
    """
    parts = [card.to_system_prompt()]
    parts.append(state.to_prompt_block())
    parts.append(CHANNEL_CONTEXT.get(channel, CHANNEL_CONTEXT["im"]))

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


def _fuzzy_ify(text: str) -> str:
    """噪声注入（4.4）：精确数字/日期模糊化——「完美的精确度就是非人感」。

    仅低确信档调用。规则覆盖常见时间/数量表达，保持语义可懂。
    """
    import re

    t = text
    # 上周三 / 三月五号 → 上次
    t = re.sub(r"(上|这|前|大前|上上)(周|星期)([一二三四五六日天])", "上次", t)
    # 3月5日 / 3.5 / 03-05 → 上个月那几天（仅当是过去式语境时语义仍通顺）
    t = re.sub(r"\d{1,2}月\d{1,2}日?", "上个月那几天", t)
    # 3小时 / 45分钟 / 2天 / 一周 / 3年 → 好一阵子 / 那阵子（完全模糊，去掉精确感）
    t = re.sub(r"\d+\s*(小时|分钟|秒钟?)", "好一阵子", t)
    t = re.sub(r"\d+\s*(天|周|个月|年)", "那阵子", t)
    # 3点20分 / 3:20 → 那会儿
    t = re.sub(r"\d{1,2}[:：]\d{2}", "那会儿", t)
    return t


def format_memory_line(entry) -> str:
    """记忆行格式化（4.4 确信度分级）：按 strength 四档措辞 + 噪声注入。

    - strength ≥0.85：自信调用「我记得你……」
    - 0.6~0.85：试探性调用「我好像记得……是……吗？还是我记串了？」
    - 0.35~0.6：模糊关联「我记得好像有这么回事……细节全糊了，你能再跟我说说吗？」+ 数字模糊化
    - <0.35：隐约记得（基本不注入，build_system_prompt 已过滤）
    - meta.emotion 存在时附加（"你提起时听起来很开心"）
    """
    if entry.strength >= 0.85:
        verb = "我记得"
        content = entry.content
    elif entry.strength >= 0.6:
        verb = "我好像记得"
        content = entry.content + "……是……吗？还是我记串了？"
    elif entry.strength >= 0.35:
        verb = "我记得好像有这么回事"
        content = _fuzzy_ify(entry.content) + "……细节全糊了，你能再跟我说说吗？"
    else:
        verb = "我隐约记得"
        content = entry.content
    line = f"- {verb}：{content}"
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

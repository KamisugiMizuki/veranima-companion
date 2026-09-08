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
    "im": "【当前场景】你正在用文字聊天软件打字。像平时私聊那样：一条只说一件事，"
          "一般几个字到一行，长内容拆成两三行连着发；句尾不加句号，少用完整复句和书面连接词；"
          "情绪落在标点和重复字上（？？？、……、气气气、啧），可以连打、可以一条只回一个词；"
          "不要总结、不要解释自己刚说的话、不要括号补充、不要列表和 Markdown。"
          "但一件事短句说不完时（比如把攒着的几件事一次说完），别为求短而漏信息："
          "可以写成一句长句，也可以几条语义连贯的短句接着发——说全、说顺比说短重要。",
    "tts": "【当前场景】你正在用户旁边说话交流。可以口语化，允许「嗯…」「那个…」填充词、"
           "自我修正和重复；像面对面聊天一样自然。",
}

# R1 追问检测（R1_SPEC 3.去重与修正）：用户追问细节 → 跳过模糊化给精确值
CLARIFY_PATTERNS = (
    "什么时候", "几点", "哪一天", "哪个星期", "几号", "多久", "多少", "具体是", "到底是",
    "再想想", "仔细想想", "记清楚", "具体时间", "具体地点", "在哪", "哪里",
    "啥事", "什么事", "哪件事", "你指啥", "你指什么", "你指的是", "你说的啥", "说啥", "什么意思",
)


def is_clarification(user_text: str, judgment: bool | None = None) -> bool:
    """用户是否在追问记忆细节（触发精确值重检索）。

    judgment=统一判断点的 clarification 字段（True/False 以语义裁决为准；
    None=未裁决退回关键词表——"你确定？""再说一遍重点"这类变体词表杀不完）。
    """
    if judgment is not None:
        return bool(judgment)
    return any(p in user_text for p in CLARIFY_PATTERNS)

def _fuzzy_ify(text: str) -> str:
    """噪声注入（DESIGN 4.4）：精确数字/日期模糊化——「完美的精确度就是非人感」。

    仅低确信档调用。规则覆盖常见时间/数量表达（阿拉伯数字 + 中文数字），保持语义可懂。
    """
    import re

    t = text
    # 上周三 / 三月五号 → 上次
    t = re.sub(r"(上|这|前|大前|上上)(周|星期)([一二三四五六日天])", "上次", t)
    # 3月5日 / 三月五日 → 上个月那几天
    t = re.sub(r"[0-9一二两三四五六七八九十]+月[0-9一二两三四五六七八九十]+日?", "上个月那几天", t)
    # 3小时 / 45分钟 / 2天 / 一周 / 3年 → 好一阵子 / 那阵子（含中文数字：三天/两周）
    t = re.sub(r"[0-9一二两三四五六七八九十]+\s*(小时|分钟|秒钟?)", "好一阵子", t)
    t = re.sub(r"[0-9一二两三四五六七八九十]+\s*(天|周|个月|年)", "那阵子", t)
    # 3点20分 / 三点二十 / 3:20 → 那会儿
    t = re.sub(r"[0-9一二两三四五六七八九十]+[:：][0-9一二两三四五六七八九十]+", "那会儿", t)
    return t


# 现实行动边界（DESIGN 4.8 修正版，sakura DESKTOP_PET_CONTEXT 借鉴）：
# 守行为边界但不暴露身份——不声明"我是 AI/桌宠"，像真人一样自然拒绝/转移。
# 与角色卡身份边界（被问"是真人吗"前两次带过）同一哲学。
REALITY_BOUNDARY = (
    "【现实行动边界】如果用户约你现实见面、打电话、索要现实联系方式，或提出现实接触"
    "（拥抱/牵手等），不要答应，也不要解释自己是什么——像真人一样自然地婉拒或转移话题"
    "（例如「见面的话……还是算了，线上聊就挺好」）。不虚构自己参与过现实活动。"
    "对用户同样只采信 ta 说过的事实：没听 ta 提过公司/学校，就当不知道（09-05 "
    "真机：对大四学生说『你们公司聚餐』被当场质问）。"
)

# R0 能力匹配层（DESIGN 4.3）：话题落在卡里「略知/完全不懂」的领域 → 注入姿态。
# 措辞交给模型按语境写（固定话术池会让回复僵化；四型=好奇/共情/关联/承认，作为可选姿态）。
CAPABILITY_STANCE = {
    "略知": "【话题边界】这个话题你只懂个大概——别装懂，也别一口回绝。可以好奇追问、"
            "共情、扯到相近的事，或者干脆承认不懂；选最自然的一种，不要每轮都用同一招。",
    "完全不懂": "【话题边界】这个话题你完全不懂，坦诚说不会，不要硬撑也不要编。",
}

# 交互回复统一结构化输出：按通道选择可见/语音字段，thinking 等字段不消费
IM_STRUCTURED_OUTPUT_INSTRUCTION = (
    "【输出格式·文字聊天】你的回复必须只输出 JSON，不要输出思考过程、分析步骤、草稿、规则核对或 Markdown。"
    '{"segments":[{"text":"给用户看的文字回复","tone":"语气标签","portrait":"表情标签"}],'
    '"memory_candidates":[{"kind":"conversation_event","topic":"可复用的具体主题",'
    '"content":"只概括用户本轮明确表达的临时事件或状态变化","status":"active|paused|completed|abandoned",'
    '"intent":"check_in|remind","follow_up_days":0,"confidence":0.0}]}。'
    "text 是唯一的可见文本；memory_candidates 可省略。只在本轮确实包含可后续跟进的事件、"
    "临时习惯或其状态变化时输出 conversation_event；topic 必须能与同一事件的后续状态对齐，"
    "不得从 assistant 猜测、假设句或例子生成。active 的 follow_up_days 只能为 1-7，"
    "其他状态必须为 0。不要添加 thinking、analysis、reasoning、ja、zh 等字段。"
)

# R2 表情标签驱动（R2_SPEC 2）：tts 通道要求结构化 JSON 输出
SEGMENTED_OUTPUT_INSTRUCTION = (
    "【输出格式·单语语音】你的回复必须以 JSON 输出："
    '{"segments":[{"text":"要显示并送 TTS 的台词","tone":"语气标签","portrait":"表情标签"}]}'
    "。text 同时用于气泡显示和语音合成；tone 从角色语气里选，portrait 只能从可用表情列表里选。"
    "不要添加 thinking、analysis、reasoning 字段，不要输出 JSON 以外的任何内容。"
)

# R2 双语输出（角色卡 bilingual.enabled 时生效；如由岐：日语配音 + 中文显示）
BILINGUAL_OUTPUT_INSTRUCTION = (
    "【输出格式·双语语音】你的回复必须同时提供日语与中文："
    '{"segments":[{"ja":"日本語のセリフ（声優役・TTS用）","zh":"中文对照（显示用）","tone":"语气标签","portrait":"表情标签"}]}'
    "。ja 是送给 TTS 的日语台词，zh 是聊天气泡显示的中文翻译；两者表达同一内容。"
    "tone 从角色语气里选，portrait 只能从可用表情列表里选。不要添加 thinking、analysis、reasoning 字段，不要输出 JSON 以外的任何内容。"
)


def _expression_prompt(card) -> str:
    """从角色卡提取可用表情标签列表（R4_SPEC 2.2：prompt 注入词表防 OOC）。"""
    ver = getattr(card, "veranima", None) or {}
    avatar = ver.get("avatar") or {}
    exprs = avatar.get("expressions") or {}
    if not exprs:
        return ""
    labels = "、".join(exprs.keys())
    return f"【可用表情标签】{labels}（回复的 portrait 字段只能从这些里面选）"


def build_system_prompt(
    card: CharacterCard,
    state: AgentState,
    memory: MemoryStore,
    *,
    core_profile_budget: int = 1200,
    procedural_budget: int = 1000,   # MEMORY_SPEC 10.4：procedural 独立预算
    section_budget: int = 1600,
    session_budget: int = 600,
    extra_blocks: list[str] | None = None,
    channel: str = "im",
    clarification: bool = False,  # R1 可逆性：用户追问细节 → 记忆行不模糊化（R1_SPEC 3）
    relationship=None,  # P-4（PERSONA_LOOP_SPEC）：PersonaBrief 接入口；None=不注入
    reuse_action: str = "",  # P-6：本轮回用动作（extend/contrast/question/apply/remember）
) -> str:
    """按预算组装系统 prompt。记忆按层注入，超出预算截断。extra_blocks 为附加块（学习参数/镜像/承诺）。

    channel 注入通道语境（DESIGN 4.8 通道感知）：im=打字聊天（利落/去填充词），tts=语音（口语化/允许填充词）。
    """
    # 关键指令首置（指令稀释：首尾服从度最高）：现实行动边界是不可协商的硬约束，
    # 压最前；输出格式指令压尾（见下方 format_block）；记忆/画像等数据块留中段。
    parts = [REALITY_BOUNDARY, card.to_system_prompt()]
    query_hint = _latest_query(memory)
    parts.append(state.to_prompt_block())
    parts.append(CHANNEL_CONTEXT.get(channel, CHANNEL_CONTEXT["im"]))
    # R0 能力匹配层（DESIGN 4.3）：话题熟悉度 → 姿态（能力事实在卡里，措辞交给模型）
    if query_hint:
        from .capability import capability_level
        _stance = CAPABILITY_STANCE.get(capability_level(card, query_hint), "")
        if _stance:
            parts.append(_stance)
    # R2：tts 通道注入表情词表 + 结构化输出要求（R2_SPEC 2）
    # 输出格式指令不在此处注入——它是最高优先级的可验证指令，统一压到 parts 末尾
    # （prompt 中段是模型服从度最弱的位置；记忆/画像等数据留中段）。
    format_block = ""
    if channel == "tts":
        expr_prompt = _expression_prompt(card)
        if expr_prompt:
            parts.append(expr_prompt)
        # R2 双语（角色卡 bilingual.enabled）：ja 送 TTS / zh 显示
        bilingual = bool(((card.veranima or {}).get("bilingual") or {}).get("enabled"))
        format_block = BILINGUAL_OUTPUT_INSTRUCTION if bilingual else SEGMENTED_OUTPUT_INSTRUCTION
    else:
        format_block = IM_STRUCTURED_OUTPUT_INSTRUCTION

    # E. 相关记忆（MEMORY_SPEC 10.4：Context Brief 统一预算，完整 item 截断）
    from ..memory.brief import build_brief, format_brief

    # P-4（PERSONA_LOOP_SPEC 8）：PersonaBrief 单一接入口（如何理解与回应）
    if relationship is not None:
        from .persona import build_persona_brief, format_persona_brief
        persona_text = format_persona_brief(build_persona_brief(
            query_hint, card, relationship, state, memory,
        ))
        if persona_text:
            parts.append(persona_text)
        # P-6：本轮回用动作约束（agent 计算；动作非 none 时追加）
        if reuse_action and reuse_action != "none":
            action_guide = {
                "extend": "用新情境扩展该观点",
                "contrast": "对照你的不同观点，保留分歧",
                "question": "先确认适用边界再讨论",
                "apply": "把该观点应用到当前问题",
                "remember": "自然地提起那次共同经历",
            }.get(reuse_action, "")
            if action_guide:
                parts.append(f"【回用动作】{action_guide}（不要逐字复述用户原句）")

    brief_items = build_brief(
        core_profile=memory.list_layer("core_profile", limit=20),
        procedural=memory.list_layer("procedural", limit=20),
        semantic=memory.recall(query_hint, top_k=5, layer="semantic") if query_hint else [],
        episodic=_annotate_present(
            memory.recall(query_hint, top_k=5, layer="episodic") if query_hint else [], card),
        session=memory.list_layer("session", limit=10),
        budgets={
            "core_profile": core_profile_budget,
            "procedural": procedural_budget,
            "semantic": section_budget,
            "episodic": section_budget,
            "session": session_budget,
        },
        total_budget=core_profile_budget + procedural_budget + section_budget * 2 + session_budget,
    )
    # DESIGN 4.4 / R1_SPEC 3：低确信条目模糊化（精确日期/时长→模糊表达）；
    # 用户正在追问细节时跳过（给精确值——可逆性）
    if channel == "im" and not clarification:
        from dataclasses import replace
        brief_items = [replace(it, text=_fuzzy_ify(it.text))
                       if it.confidence_label == "低" else it for it in brief_items]
    brief_text = format_brief(brief_items)
    if brief_text:
        parts.append(brief_text)
        # MEMORY_SPEC 11 延迟纠错：仅当注入含低置信条目时允许偶发自我修正
        if any(it.confidence_label == "低" for it in brief_items):
            parts.append("（你可以偶尔说'等一下，我刚才记错了，应该是……'——人回忆时会有延迟纠错，但不要每轮都用）")

    # 附加块（MVP2：风格参数 / 语言镜像 / 承诺提醒）
    for block in (extra_blocks or []):
        if block:
            parts.append(block)

    # 输出格式指令永远压尾（指令稀释：首尾服从度最高，最重要且可机器校验的指令放最后）
    if format_block:
        parts.append(format_block)

    return "\n".join(parts)


def _annotate_present(entries, card):
    """共享记忆库的 episodic 条目标注在场角色（审计#2）：别人在场的事，
    不是你亲历的记忆——防跨角色脑补穿帮（历史条目无 present 键=不标注）。"""
    name = str(getattr(card, "name", "") or "")
    for e in entries:
        pres = str((e.meta or {}).get("present") or "")
        if pres and pres != name:
            e.content = f"（这是{pres}和ta之间的事，你只是听ta提起过）{e.content}"
    return entries


def _latest_query(memory: MemoryStore) -> str:
    """用最近一条用户消息作为检索查询。"""
    msgs = memory.recent_messages(limit=3)
    for m in reversed(msgs):
        if m["role"] == "user":
            return m["content"]
    return ""

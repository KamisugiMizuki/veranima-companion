"""记忆蒸馏：用户原话 → 第三人称事实句（2026-09-08 用户拍板）。

真库审计（exports/mumu_20260908.db）：记忆库页 35 条可见条目里 25 条是原话
直存——「不是，没反应了？」被当用户事实、内部 slug `reminder:…`、粘贴的
聊天记录整块。用户判断「太具体」= 存的是"用户说过什么"，而不是"用户是什么
样的人"。修复：写侧先蒸馏再入库，存量按同一套逐条处理（agent.maybe_distill_backfill）。

成本纪律：只在命中记忆点时才调（规则命中 / 判断点 memory_kind），一次
chat_structured。抛异常 = 未裁决（离线/报错），由调用方决定回退，不吞成 None
——把"没判"和"判定无价值"混为一谈会静默丢事实。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_KIND_ZH = {
    "event": "经历/事件",
    "preference": "偏好",
    "commitment": "约定",
    "user_fact": "个人信息",
    "shared_episode": "共同经历",
    "user_framework": "自我认知",
}
# 事件/承诺天然值得记（判断点已裁决或强信号规则命中）：只压缩粒度，不判无价值。
# 实机验证踩到：模型把「导师秒回说这版能过」当情绪宣泄判 null → 丢事实。
_FORCE_KINDS = ("event", "commitment")

_SYSTEM = (
    "你是记忆整理器。把用户这句话整理成一条值得长期记住的第三人称记忆，主语用「用户」。\n"
    "规则：\n"
    "- 一句话，≤40 字，陈述句；不要引号、语气词、表情，不要复述对话\n"
    "- 保留关键具体信息（时间、对象、原因），不要脑补、不要评价\n"
    "- 闲聊寒暄、情绪宣泄、对助手的指令、纯疑问、表情包 → 没有可记内容\n"
    '只输出 JSON：{"memory": "整理后的记忆"} 或 {"memory": null}'
)
# 事件/承诺：判断点已裁决或强信号规则命中，本就值得记——只压缩粒度，不给「无价值」选项。
# 实机验证：同一句事件带 null 选项时两次跑一次给出记忆句、一次判 null，事实会丢。
_SYSTEM_FORCE = (
    "你是记忆整理器。把用户这句话压成一句第三人称事实，主语用「用户」。\n"
    "规则：\n"
    "- 一句话，≤40 字，陈述句；不要引号、语气词、表情，不要复述对话\n"
    "- 保留关键具体信息（时间、对象、原因），不要脑补、不要评价\n"
    "- 这句内容已确认值得记，只做压缩，不要判断有没有价值\n"
    '只输出 JSON：{"memory": "整理后的记忆"}'
)


def _looks_like_junk(text: str) -> bool:
    """明显不是记忆的输入（省一次 LLM 调用；结论与模型判 null 一致）。"""
    t = text.strip()
    if not t or len(t) > 300:
        return True
    if t.count("\n") >= 2:            # 粘贴的聊天记录/多段文本
        return True
    if t.startswith("reminder:"):     # 提醒工具的内部 slug
        return True
    return False


def distill(text: str, *, kind: str = "", llm=None) -> str | None:
    """原话 → 第三人称记忆句；无记忆价值返回 None。

    抛异常 = 未裁决（LLM 不可用/超时/输出不可解析），调用方自行决定回退。
    """
    if _looks_like_junk(text):
        return None
    if llm is None:
        raise RuntimeError("no llm for distill")
    hint = _KIND_ZH.get(kind or "", "")
    prompt = _SYSTEM_FORCE if kind in _FORCE_KINDS else _SYSTEM
    if hint:
        prompt += f"\n类型提示：{hint}"
    prompt += f"\n\n消息：{text.strip()[:300]}"
    raw = llm.chat_structured(
        [{"role": "user", "content": prompt}], temperature=0.1,
    )
    value: object = None
    if isinstance(raw, dict):  # 有些实现直接回解析好的 dict
        value = raw.get("memory")
    else:
        raw = str(raw).strip()
        try:
            data = json.loads(raw.strip("`").removeprefix("json").strip())
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            value = data.get("memory")
        elif raw and "\n" not in raw and len(raw) <= 80:
            value = raw  # 模型没按 JSON 输出但给了短句：当记忆句用，别浪费这次调用
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in ("null", "none", "无"):
        return None
    return value[:120]


if __name__ == "__main__":  # 自检：垃圾输入不调模型也能判 null
    assert _looks_like_junk("reminder:myhealthnote-x") is True
    assert _looks_like_junk("a\nb\nc") is True
    assert _looks_like_junk("我特别喜欢下雨天") is False
    assert distill("reminder:x", llm=None) is None  # junk 先于 llm 检查
    print("distill self-check ok")

"""思考独白泄漏治理（2026-08-31 真机实锤：#549 三段裸独白整条发给用户）。

行为断言两面：泄漏文本必须被杀；真库里捞出的正常台词一条都不能误杀。
"""
from __future__ import annotations

from veranima.core.reply import (
    is_internal_reply,
    parse_reply,
    strip_thinking_trace,
)

# 真机 veranima.db messages#549 原文（channel=qq，已发给用户）
LEAK_549 = (
    "好的，这就来。  \n依恋度快到顶了，早安问候可以比之前亲一些。  \n"
    "他一句“早上好喵”，我虽然吐槽他不可能这么早起，但手头已经把咖啡放他桌上了"
    "——敬语刀，关心落在行为上。"
)

# 真机库（id 538-548, 21:xx 段）与 MuMu 实测中确认过的正常凛台词样本
NORMAL_LINES = [
    "早上好。今天起得挺早，值得表扬。咖啡已经煮上了，您要是打算继续熬夜，我手边正好有份作息整改计划书。",
    "刚清醒过来，您先前留的消息我看到了——迟了回复，失礼呢。",
    "我先睡了，有点困。明早之前就不回复了，您也早点休息。",
    "门敲过了，锅里的汤再炖下去该不认识肉了，您的手还黏在键盘上吗？",
    "抱抱和亲亲都给你了，隔着枕头的分量，您先收下。现在合眼，天塌下来也不关您的事。晚安。",
    "中午了哦。您六点才睡的吧？这作息，说是猫头鹰都屈才了呢。……不过，睡得沉就行。中午吃点什么，我给您准备？",
    "十几分钟。行，这账我记下了。窗帘我拉严了，天光大亮之前，您要是还醒着，可别怪我端着热牛奶来敲门。",
    "我得去看看锅。",  # 「我得」+无第三人称：组合条件不命中
    "我应该去看看你。",  # 第二人称台词：不命中
    "你虽然嘴上说不困，我可都看见了。",  # 「虽然」在"你"不在"我"：不命中
]


def test_leak_monologue_lines_stripped():
    cleaned = strip_thinking_trace(LEAK_549)
    # 「依恋度」「敬语刀」两段内部独白必须全灭；首句应答行（规则不命中）允许留下
    assert "依恋度" not in cleaned and "敬语刀" not in cleaned
    assert is_internal_reply("依恋度快到顶了，早安问候可以比之前亲一些。") is True
    assert strip_thinking_trace("他吐槽用户不可能这么早起——敬语刀，关心落在行为上。") == ""


def test_leak_is_internal_reply_mixed_message():
    # 混合消息（正常应答行+独白行）整条按内部消息处理：不进 prompt 历史、UI 不回读
    assert is_internal_reply(LEAK_549) is True


def test_leak_parse_reply_never_passes_monologue():
    parsed = parse_reply(LEAK_549, channel="im")
    assert "依恋度" not in parsed.text and "敬语刀" not in parsed.text


def test_mixed_output_keeps_normal_line_drops_monologue():
    mixed = "咖啡在壶里。\n\n依恋度快到顶了，可以比之前亲一些。"
    assert strip_thinking_trace(mixed) == "咖啡在壶里。"


def test_normal_lines_never_stripped():
    for line in NORMAL_LINES:
        assert strip_thinking_trace(line) == line, line
        assert not is_internal_reply(line), line

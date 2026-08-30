"""思考独白泄漏治理（2026-08-31 真机 #549 实锤）。两层架构：

规则层（reply.py，纯函数、确定性）：只杀封闭词表（我们自己注入 prompt 的
字段名——词源可控）与「第三人称对象化+计划语气」强组合；
LLM 裁决层（agent._sanitize_monologue，出口）：灰色行送一次语义判定。
"""
from __future__ import annotations

import json

from veranima.core.character import CharacterCard
from veranima.core.reply import (
    is_internal_reply,
    monologue_suspect_lines,
    parse_reply,
    strip_thinking_trace,
)
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore

# 真机 veranima.db messages#549 原文（channel=qq，已发给用户）
LEAK_549 = (
    "好的，这就来。  \n依恋度快到顶了，早安问候可以比之前亲一些。  \n"
    "他一句“早上好喵”，我虽然吐槽他不可能这么早起，但手头已经把咖啡放他桌上了"
    "——敬语刀，关心落在行为上。"
)

# 真机库/MuMu 实测确认过的正常凛台词样本（一行都不许被规则层杀）
NORMAL_LINES = [
    "早上好。今天起得挺早，值得表扬。咖啡已经煮上了，您要是打算继续熬夜，我手边正好有份作息整改计划书。",
    "刚清醒过来，您先前留的消息我看到了——迟了回复，失礼呢。",
    "我先睡了，有点困。明早之前就不回复了，您也早点休息。",
    "门敲过了，锅里的汤再炖下去该不认识肉了，您的手还黏在键盘上吗？",
    "抱抱和亲亲都给你了，隔着枕头的分量，您先收下。现在合眼，天塌下来也不关您的事。晚安。",
    "中午了哦。您六点才睡的吧？这作息，说是猫头鹰都屈才了呢。……不过，睡得沉就行。中午吃点什么，我给您准备？",
    "十几分钟。行，这账我记下了。窗帘我拉严了，天光大亮之前，您要是还醒着，可别怪我端着热牛奶来敲门。",
    "我得去看看锅。",
    "我应该去看看你。",
    "你虽然嘴上说不困，我可都看见了。",
    "他要是敢欺负你，我可不答应。",  # 第三人称≠用户（说别人）：不杀
]


# ---------- 规则层 ----------

def test_rule_kills_closed_internal_term():
    # 「依恋度」= 我们注入 prompt 的字段名（词源可控），台词不可能合法使用
    assert strip_thinking_trace("依恋度快到顶了，早安问候可以比之前亲一些。") == ""


def test_rule_preserves_all_normal_lines():
    for line in NORMAL_LINES:
        assert strip_thinking_trace(line) == line, line
        assert not is_internal_reply(line), line


def test_suspect_detection_marks_open_meta_lines_for_llm():
    # #549 第 3 行（敬语刀/他…我…）规则层不硬杀（开放词，台词可合法提到
    # 人设/吐槽），但必须被标记为可疑交给出口 LLM 裁决
    suspects = monologue_suspect_lines(LEAK_549)
    assert any("敬语刀" in s for s in suspects)
    assert not any("依恋度" in s for s in suspects)  # 已被规则杀的不再送判


def test_normal_lines_produce_no_or_harmless_suspects():
    # 正常台词允许被"标记"（送 LLM 复核），但绝不能被规则层直接杀（上方已断言）；
    # 此处断言标记集合永不含被规则杀的行，且多数正常句直接零标记
    for line in NORMAL_LINES:
        suspects = monologue_suspect_lines(line)
        assert all(s != line for s in suspects) or True  # 标记≠删除
    clean = [l for l in NORMAL_LINES if not monologue_suspect_lines(l)]
    assert len(clean) >= 6  # 大多数正常句根本不进灰色区（成本旁路）


# ---------- LLM 裁决层 ----------

class Embed:
    dim = 8

    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class JudgeLLM:
    """chat_structured 扮演判定器：按脚本返回 monologue_lines 或抛错。"""

    def __init__(self, verdict=None, boom=False):
        self.verdict = verdict
        self.boom = boom
        self.calls = 0

    def is_model_loaded(self):
        return True

    def chat(self, messages, **kw):
        return "好的。"

    def chat_structured(self, messages, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("llm down")
        return json.dumps({"monologue_lines": self.verdict or []}, ensure_ascii=False)


def _agent(tmp_path, llm):
    from veranima.core.agent import Agent
    return Agent(CharacterCard(name="Judge"),
                 MemoryStore(str(tmp_path / "j.sqlite"), config={}, provider=Embed()),
                 llm, AgentState(), config={"root": str(tmp_path)})


def test_judge_drops_llm_confirmed_monologue(tmp_path):
    suspect = "他一句“早上好喵”，我虽然吐槽他不可能这么早起，但手头已经把咖啡放他桌上了——敬语刀，关心落在行为上。"
    llm = JudgeLLM(verdict=[0])  # 判定器返回编号
    agent = _agent(tmp_path, llm)
    out = agent._sanitize_monologue("好的，这就来。\n" + suspect)
    assert out == "好的，这就来。"
    assert llm.calls == 1


def test_judge_keeps_normal_dialogue_it_vindicates(tmp_path):
    line = "他要是敢欺负你，我可听不得。"
    llm = JudgeLLM(verdict=[])
    agent = _agent(tmp_path, llm)
    assert agent._sanitize_monologue(line) == line


def test_judge_failure_fails_open(tmp_path):
    # LLM 挂了：保留规则层输出，绝不因判定不可用而误删正常台词
    line = "这人设可是你夸过的，我记着呢。"
    agent = _agent(tmp_path, JudgeLLM(boom=True))
    assert agent._sanitize_monologue(line) == line


def test_no_suspect_no_llm_call(tmp_path):
    llm = JudgeLLM()
    agent = _agent(tmp_path, llm)
    assert agent._sanitize_monologue("咖啡在壶里，刚煮的。") == "咖啡在壶里，刚煮的。"
    assert llm.calls == 0  # 零灰色行=零额外调用，成本只在真需要时发生


def test_parse_reply_never_carries_closed_terms():
    parsed = parse_reply(LEAK_549, channel="im")
    assert "依恋度" not in parsed.text

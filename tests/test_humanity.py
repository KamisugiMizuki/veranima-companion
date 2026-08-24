"""人性化瞬间测试（DESIGN 8.7）：考古 / 记得感分级 / 情感色彩 / 个性化问候 / 状态暗示。"""

from __future__ import annotations

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.core.prompts import format_memory_line
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    """捕获最后一次 prompt，可编程回复。"""

    def __init__(self, reply="（主动）对了，你上次说的那个面试后来怎么样？"):
        self.reply = reply
        self.last_prompt = ""
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        self.last_prompt = messages[-1]["content"]
        return self.reply

    def is_model_loaded(self):
        return True

    low_energy_max_tokens = 256


@pytest.fixture
def agent(tmp_path):
    card = CharacterCard(name="小V", description="测试", personality="温柔")
    return Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=FakeLLM(),
        state=AgentState(),
        config={},
    )


# ---------- 8.7.1 主动考古 ----------

def test_dig_old_memory_returns_old_event(agent):
    agent.memory.store("episodic", "用户说想学吉他，还买了一把", importance=0.8, confidence=0.6)
    old = agent._dig_old_memory()
    assert old is not None
    assert "吉他" in old


def test_dig_old_memory_none_when_empty(agent):
    assert agent._dig_old_memory() is None


def test_proactive_removed_by_r4(agent):
    """R4_SPEC 3：idle/fatigue 类无理由主动已关闭（_try_proactive 返回空）。"""
    agent.memory.store("episodic", "用户说想学吉他", importance=0.8, confidence=0.6)
    msg = agent._try_proactive()
    assert msg == ""


def test_proactive_fallback_when_no_memory(agent):
    """R4 已废弃路径：无理由主动不再生成（返回空，不调 LLM）。"""
    llm = agent.llm
    msg = agent._try_proactive()
    assert msg == ""
    assert llm.calls == 0


# ---------- 8.7.2 记得感分级 ----------

def test_format_memory_high_strength():
    e = MemoryEntryStub(strength=0.9, content="你喜欢下雨天", meta={})
    line = format_memory_line(e)
    assert line.startswith("[记忆|置信度:高] 我记得")  # R1_SPEC 4 注入格式
    assert "下雨天" in line


def test_format_memory_tentative_strength():
    e = MemoryEntryStub(strength=0.7, content="你喜欢下雨天", meta={})
    line = format_memory_line(e)
    assert line.startswith("[记忆|置信度:中] 我好像记得")
    assert "记串了" in line


def test_format_memory_fuzzy_strength():
    e = MemoryEntryStub(strength=0.5, content="你上周三说加班3小时", meta={})
    line = format_memory_line(e)
    assert line.startswith("[记忆|置信度:低] 我记得好像有这么回事")
    assert "细节全糊了" in line
    assert "上周三" not in line  # 噪声注入：精确日期已模糊化
    assert "3小时" not in line   # 噪声注入：精确时长已模糊化


def test_format_memory_low_strength():
    e = MemoryEntryStub(strength=0.3, content="你喜欢下雨天", meta={})
    assert format_memory_line(e).startswith("[记忆|置信度:低] 我隐约记得")


def test_format_memory_with_emotion():
    e = MemoryEntryStub(strength=0.9, content="用户喜欢蓝色", meta={"emotion": "很开心"})
    line = format_memory_line(e)
    assert "很开心" in line


# ---------- 8.7.2 情感色彩提取 ----------

def test_extract_emotion_detected(agent):
    agent.handle("哈哈我特别喜欢下雨天")
    sem = agent.memory.list_layer("semantic")
    assert any((e.meta or {}).get("emotion") == "很开心" for e in sem)


# ---------- 8.7.4 离线思考（迟来的回应） ----------

def test_late_reply_uses_last_user_msg(agent):
    agent.memory.store_message("user", "下周要去面试了，有点紧张", 80, "平静")
    llm = agent.llm
    msg = agent.late_reply()
    assert llm.calls == 1
    assert "面试" in llm.last_prompt  # 最近一条用户消息进了生成 prompt
    assert msg


def test_late_reply_strips_structured_json_before_persisting(agent):
    agent.memory.store_message("user", "昨天我认真回答了一个问题", 80, "平静")
    agent.llm.reply = '{"segments":[{"text":"刚醒。昨天你那么认真地回答，我可是好好记着了。","tone":"率直","portrait":"微笑"}]}'
    msg = agent.late_reply()
    assert msg == "刚醒。昨天你那么认真地回答，我可是好好记着了。"
    assert "segments" not in msg
    assert agent.memory.recent_messages(limit=1)[0]["content"] == msg


def test_late_reply_does_not_echo_truncated_structured_json(agent):
    agent.memory.store_message("user", "今天继续推进高数", 80, "平静")
    agent.llm.reply = (
        '**\n    `{"segments":[{"text":"今天的高数推得怎么样了？要是头大就先搁着，不用急着回我。",'
        '"tone":"慵懒","portrait'
    )
    msg = agent.late_reply()
    assert "segments" not in msg
    assert "portrait" not in msg
    assert msg


def test_late_reply_does_not_persist_failure_fallback(agent):
    agent.memory.store_message("user", "今天继续推进高数", 80, "平静")
    agent.llm.reply = "（我好像还没醒过来……服务没在运行。检查一下 API 配置？）"
    msg = agent.late_reply()
    assert msg != "（我好像还没醒过来……服务没在运行。检查一下 API 配置？）"
    assert all(
        "服务没在运行" not in row["content"]
        for row in agent.memory.recent_messages(limit=4)
    )


def test_late_reply_fallback_without_llm(agent):
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    agent.llm = FakeLLM()
    agent.llm.is_model_loaded = lambda: False
    msg = agent.late_reply()
    assert msg  # 模板池降级


def test_late_reply_skips_when_conversation_closed(agent):
    """最后一条是 assistant（对话已闭合/已回应完）→ 不触发迟来回应。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    agent.memory.store_message("assistant", "加油，你肯定可以的", 80, "平静")
    llm = agent.llm
    assert agent.late_reply() == ""
    assert llm.calls == 0  # 不消耗 LLM 调用


def test_late_reply_fallback_template_dedup(agent):
    """模板降级：最近已出现过的模板不重复发（防同一条连发刷屏）。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    agent.llm = FakeLLM()
    agent.llm.is_model_loaded = lambda: False
    first = agent.late_reply()
    assert first
    # 再补一条 user 消息使对话重新未闭合，再触发：不得与上一条相同
    agent.memory.store_message("user", "还有个问题想问", 80, "平静")
    # 清除 QQ 通道自己的发言间隔；模板去重仍由历史内容负责。
    agent.gate._last_any.clear()
    second = agent.late_reply()
    assert second
    assert second != first


def test_late_reply_low_energy_skip(agent):
    agent.state.energy = 10
    assert agent.late_reply() == ""


def test_late_reply_stored_to_memory(agent):
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    agent.late_reply()
    recent = agent.memory.recent_messages(limit=3)
    assert any(m["role"] == "assistant" for m in recent)


# ---------- 8.7.5 个性化问候 ----------

def test_greeting_uses_recent_context(agent):
    agent.memory.store_message("user", "明天面试，有点紧张", 80, "平静")
    llm = agent.llm
    msg = agent.greeting_message("morning")
    assert llm.calls == 1
    assert "面试" in llm.last_prompt
    assert msg


def test_greeting_fallback_without_llm(agent):
    agent.llm = FakeLLM()
    agent.llm.is_model_loaded = lambda: False
    msg = agent.greeting_message("noon")
    assert "中午" in msg  # 回退模板


# ---------- 8.7.6 状态暗示 ----------

def test_low_energy_prompt_hints_action(agent):
    agent.state.energy = 20
    block = agent.state.to_prompt_block()
    assert "哈欠" in block


def test_normal_energy_no_action_hint(agent):
    agent.state.energy = 60
    block = agent.state.to_prompt_block()
    assert "哈欠" not in block


class MemoryEntryStub:
    """format_memory_line 的轻量替身（只暴露用到的字段）。"""

    def __init__(self, strength, content, meta):
        self.strength = strength
        self.content = content
        self.meta = meta

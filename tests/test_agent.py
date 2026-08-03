"""对话引擎错误容忍测试：LLM 不可用 / 生成失败时不崩溃，返回角色化兜底。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.llm.client import LLMError, LLMUnavailableError
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    """可编程假 LLM：按设定抛错或返回固定回复。"""

    def __init__(self, error=None, reply="正常回复", loaded=True):
        self.error = error
        self.reply = reply
        self.loaded = loaded
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply

    def is_model_loaded(self):
        return self.loaded

    low_energy_max_tokens = 256


@pytest.fixture
def agent(tmp_path):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(
        db_path=str(tmp_path / "t.db"),
        config={},
        provider=FakeEmbed(),
    )
    return card, memory


def test_handle_llm_unavailable_returns_wakeup(agent, tmp_path):
    """模型未加载（游戏模式 off）：前置检查拦截，返回唤醒文案，不发请求不触发自动重载。"""
    card, memory = agent
    llm = FakeLLM(error=LLMUnavailableError("model not loaded"), loaded=False)
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={})
    r = a.handle("在吗？")
    assert "还没醒" in r.reply
    assert "run_lmstudio" in r.reply
    assert llm.calls == 0  # 关键：未加载时不发 chat 请求（避免 LM Studio 自动重载吃显存）
    msgs = memory.recent_messages(limit=4)
    assert len(msgs) == 2  # user + assistant 兜底
    assert msgs[-1]["role"] == "assistant"


def test_handle_model_loaded_but_chat_fails(agent):
    """模型已加载但生成异常：异常分类兜底。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(error=LLMUnavailableError("server hiccup"), loaded=True), state=AgentState(), config={})
    r = a.handle("在吗？")
    assert "还没醒" in r.reply
    assert a.llm.calls == 1


def test_handle_llm_generic_error_returns_fallback(agent):
    """LLM 在线但生成失败：返回通用兜底。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(error=LLMError("server 500")), state=AgentState(), config={})
    r = a.handle("在吗？")
    assert "有点卡" in r.reply


def test_handle_proactive_does_not_need_llm(agent):
    """主动发言是模板池，LLM 失败时仍可正常触发（不调 LLM）。"""
    card, memory = agent
    llm = FakeLLM(error=LLMUnavailableError("down"))
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={"chat": {"proactive_message_prob": 1.0}})
    r = a.handle("在吗？")
    # 主动发言触发且为模板消息
    assert r.proactive
    assert r.proactive_msg
    assert llm.calls == 1  # 只有对话生成调了 LLM，主动发言没有


def test_start_greeting_without_llm(agent):
    """start() 问候是时间模板，不依赖 LLM。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(error=LLMUnavailableError("down")), state=AgentState(), config={})
    opening = a.start()
    assert opening  # 初遇开场白或时间问候


def test_extract_events_preference(agent):
    """'我特别喜欢X' 等偏好表达 → semantic 层记忆。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.handle("我特别喜欢下雨天，下雨的时候心情会变好")
    sem = memory.list_layer("semantic")
    assert any("下雨天" in e.content for e in sem)


def test_extract_events_strong(agent):
    """'记住' 诉求 → episodic 层记忆。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.handle("记住：我的生日是3月14日")
    eps = memory.list_layer("episodic")
    assert any("生日" in e.content for e in eps)


def test_extract_events_plain_no_duplicate(agent):
    """普通闲聊（无信号词）不产生记忆，也不把消息本身当记忆。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.handle("今天天气不错")
    assert len(memory.list_layer("semantic")) == 0
    assert len(memory.list_layer("episodic")) == 0

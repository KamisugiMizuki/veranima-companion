"""联网搜索测试：SearXNG 客户端 / agent 工具调用链路（单轮 1 次搜索约束）。"""

from __future__ import annotations

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore
from veranima.tools.search import EvidencePack, SearchTrigger, SearXNGClient


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class ToolLLM:
    """模拟普通主 LLM；搜索由 Agent 的确定性闸门控制。"""

    def __init__(self, tool_calls=True, search_result="晴，25°C"):
        self.tool_calls = tool_calls
        self.search_result = search_result
        self.rounds = 0
        self.messages = []
        self.low_energy_max_tokens = 256

    def chat(self, messages, **kw):
        self.rounds += 1
        self.messages = messages
        return "我查到了：" + self.search_result

    def is_model_loaded(self):
        return True


class FakeSearch:
    def __init__(self, results=None):
        self.results = results or [{"title": "北京天气", "url": "http://x", "snippet": "晴 25°C"}]
        self.calls = 0

    def search(self, query, **kwargs):
        self.calls += 1
        return self.results

    def format_results(self, results):
        return SearXNGClient().format_results(results)


@pytest.fixture
def agent(tmp_path):
    card = CharacterCard(name="小V", description="测试", personality="温柔")
    return Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=ToolLLM(),
        state=AgentState(),
        config={"search": {"enabled": True}},
    )


def test_explicit_search_trigger_and_privacy_block():
    decision = SearchTrigger().determine("帮我查一下最近《绝区零》的更新")
    assert decision.should_search and decision.user_requested
    assert not SearchTrigger().determine("别联网，帮我查一下天气").should_search


def test_search_enabled_from_config(agent):
    assert agent.search_enabled


def test_agent_explicit_search_flow(agent):
    """显式搜索 → SearXNG → 证据进入主 LLM prompt。"""
    fake = FakeSearch()
    agent.search = fake
    r = agent.handle("帮我查一下北京天气")
    assert fake.calls == 1  # 恰好一次搜索
    assert "我查到了" in r.reply
    assert any("本轮外部信息" in str(m.get("content")) for m in agent.llm.messages if m["role"] == "system")


def test_search_disabled_no_tools(tmp_path):
    card = CharacterCard(name="小V", description="测试", personality="温柔")
    a = Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=ToolLLM(tool_calls=True),
        state=AgentState(),
        config={},  # 未开启 search
    )
    assert not a.search_enabled


def test_casual_chat_does_not_search(tmp_path):
    """普通闲聊不搜索。"""
    card = CharacterCard(name="小V", description="测试", personality="温柔")
    a = Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=ToolLLM(tool_calls=False),
        state=AgentState(),
        config={"search": {"enabled": True}},
    )
    fake = FakeSearch()
    a.search = fake
    r = a.handle("今天心情不错")
    assert fake.calls == 0
    assert "我查到了" in r.reply


def test_search_client_format():
    c = SearXNGClient()
    text = c.format_results([{"title": "T", "url": "http://u", "snippet": "S"}])
    assert "T" in text and "http://u" in text
    assert "没有返回结果" in c.format_results([])


def test_evidence_pack_is_temporary_and_source_backed():
    pack = EvidencePack.from_results("最近更新", [{"title": "公告", "url": "https://x.test/a", "snippet": "修复卡顿"}])
    prompt = pack.to_prompt()
    assert "本轮外部信息" in prompt
    assert "https://x.test/a" in prompt
    assert "长期记忆" in prompt


def test_evidence_pack_rejects_private_result_urls():
    pack = EvidencePack.from_results("内网", [{"title": "内网", "url": "http://127.0.0.1/admin", "snippet": "x"}])
    assert not pack.results

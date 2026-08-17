"""联网搜索测试：SearXNG 客户端 / agent 工具调用链路（单轮 1 次搜索约束）。"""

from __future__ import annotations

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore
from veranima.tools.search import SEARCH_TOOL, SearXNGClient


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class ToolLLM:
    """模拟带工具调用能力的 LLM：第一轮返回 search tool_call，第二轮返回最终回复。"""

    def __init__(self, tool_calls=True, search_result="晴，25°C"):
        self.tool_calls = tool_calls
        self.search_result = search_result
        self.rounds = 0
        self.low_energy_max_tokens = 256

    def chat(self, messages, **kw):
        self.rounds += 1
        return "直接回复"

    def chat_raw(self, messages, **kw):
        self.rounds += 1
        if self.tool_calls and self.rounds == 1:
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_web", "arguments": '{"query": "北京天气"}'},
                }],
            }
        return {"content": f"我查到了：{self.search_result}"}

    def is_model_loaded(self):
        return True


class FakeSearch:
    def __init__(self, results=None):
        self.results = results or [{"title": "北京天气", "url": "http://x", "snippet": "晴 25°C"}]
        self.calls = 0

    def search(self, query):
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


def test_search_tool_definition():
    assert SEARCH_TOOL["function"]["name"] == "search_web"
    assert "query" in SEARCH_TOOL["function"]["parameters"]["properties"]


def test_search_enabled_from_config(agent):
    assert agent.search_enabled


def test_agent_tool_call_flow(agent, monkeypatch):
    """模型请求搜索 → 执行 → 结果回填 → 最终回复。"""
    fake = FakeSearch()
    agent.search = fake
    r = agent.handle("北京天气怎么样？")
    assert fake.calls == 1  # 恰好一次搜索
    assert "我查到了" in r.reply


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


def test_search_no_tool_call_when_model_decides(tmp_path):
    """模型没发起 tool_call → 直接回复，不搜索。"""
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
    assert "我查到了" in r.reply  # ToolLLM(tool_calls=False) 第一轮直接返回最终内容


def test_search_client_format():
    c = SearXNGClient()
    text = c.format_results([{"title": "T", "url": "http://u", "snippet": "S"}])
    assert "T" in text and "http://u" in text
    assert "没有返回结果" in c.format_results([])

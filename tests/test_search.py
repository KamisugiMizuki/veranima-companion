"""联网搜索测试：SearXNG 客户端 / agent 工具调用链路（单轮 1 次搜索约束）。"""

from __future__ import annotations

import datetime as dt
import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore
from veranima.tools.search import (
    EvidencePack,
    SearchTrigger,
    SemanticLocator,
    SearXNGClient,
    TimeRange,
    analyze_search_intent,
    classify_search_uncertainty,
    normalize_time_in_query,
    sanitize_search_query,
)


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
        self.queries = []

    def search(self, query, **kwargs):
        self.calls += 1
        self.queries.append(query)
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


def test_implicit_freshness_trigger_and_refresh():
    trigger = SearchTrigger()
    assert trigger.determine("绝区零最近更新了什么", allow_implicit=True).should_search
    assert trigger.determine("绝区零是哪年发布的", allow_implicit=True).should_search
    assert trigger.determine("绝区零是什么", allow_implicit=True).should_search is False
    assert trigger.determine("一个新游戏是哪一年发布的", allow_implicit=True).should_search
    assert trigger.determine("绝区零有新消息吗？再查一下", allow_implicit=True).force_refresh
    assert not trigger.determine("《绝区零》是什么", allow_implicit=True, known_entities={"绝区零"}).should_search


def test_unknown_entity_fact_fallback_search():
    trigger = SearchTrigger()
    decision = trigger.determine("最近出了个叫《星痕共鸣》的游戏，你知道吗？", allow_implicit=True)
    assert decision.should_search and decision.reason == "unknown_entity"
    assert trigger.determine("你听过 Project Mugen 吗？", allow_implicit=True).should_search
    assert not trigger.determine("今天好累，陪我聊会儿", allow_implicit=True).should_search
    assert classify_search_uncertainty("一个叫《星痕共鸣》的东西是什么")["should_search"]
    assert sanitize_search_query("帮我查一下《星痕共鸣》")
    assert sanitize_search_query("我的 API key 是 sk-secret，帮我查") is None


def test_dynamic_state_intent_has_time_anchor():
    intent = analyze_search_intent("帮我找找现在明日方舟开启的活动复刻", today=dt.date(2026, 8, 22))
    assert intent.kind == "dynamic_state"
    assert intent.time_range.start.isoformat() == "2026-08-19"
    assert intent.time_range.end.isoformat() == "2026-08-23"
    assert SemanticLocator().should_upgrade(intent)
    contextual = analyze_search_intent("那个复刻活动叫什么", "我们之前聊的是《明日方舟》")
    assert contextual.entity == "明日方舟"
    assert contextual.kind == "dynamic_state"


def test_absolute_chinese_time_is_normalized_to_query_dates():
    today = dt.date(2026, 8, 22)
    cases = {
        "今年七月新番": ("2026-07-01", "2026-07-31", "2026年7月"),
        "去年发布的游戏": ("2025-01-01", "2025-12-31", "2025年"),
        "前年新闻": ("2024-01-01", "2024-12-31", "2024年"),
        "明年活动": ("2027-01-01", "2027-12-31", "2027年"),
        "后年活动": ("2028-01-01", "2028-12-31", "2028年"),
        "上个月杭州天气": ("2026-07-01", "2026-07-31", "2026年7月"),
        "五天前的新闻": ("2026-08-17", "2026-08-17", "2026年8月17日"),
        "近五天内的新闻": ("2026-08-18", "2026-08-22", "2026年8月18日"),
        "后面五天里的天气": ("2026-08-23", "2026-08-27", "2026年8月23日"),
        "三个月之前的新闻": (None, "2026-05-22", "截至2026年5月22日以前"),
        "过去三个月的新闻": ("2026-05-22", "2026-08-22", "2026年5月22日"),
    }
    for text, (start, end, marker) in cases.items():
        intent = analyze_search_intent(text, today=today)
        assert intent.time_range is not None, text
        assert intent.time_range.start and intent.time_range.start.isoformat() == start if start else intent.time_range.start is None
        assert intent.time_range.end.isoformat() == end, text
        assert marker in normalize_time_in_query(text, intent.time_range), text


def test_explicit_weather_query_uses_absolute_tomorrow_date(agent, monkeypatch):
    import veranima.tools.search as search_module
    monkeypatch.setattr(search_module, "_local_today", lambda: dt.date(2026, 8, 22))
    fake = FakeSearch()
    agent.search = fake
    agent.handle("查查明天杭州的天气预报")
    assert fake.queries and "2026年8月23日" in fake.queries[0]


def test_linkage_query_is_dynamic_and_uses_specific_search_terms():
    intent = analyze_search_intent("今年明日方舟的联动有哪些", today=dt.date(2026, 8, 22))
    assert intent.kind == "dynamic_event"
    assert intent.event_type == "联动"
    queries = SemanticLocator()._queries(intent)
    assert any("联动" in query and "合作" in query for query in queries)


def test_retry_without_operation_reuses_previous_search_topic(agent):
    fake = FakeSearch()
    agent.search = fake
    agent.search_config["allow_implicit_freshness_search"] = True
    agent.handle("找一下今年明日方舟的联动有哪些")
    agent.handle("不行不行，你再试试")
    assert fake.calls == 2
    assert "明日方舟" in fake.queries[-1]
    assert "联动" in fake.queries[-1]


def test_retry_without_operation_does_not_search_without_previous_search(agent):
    fake = FakeSearch()
    agent.search = fake
    agent.handle("你再试试")
    assert fake.calls == 0


def test_knowledge_cutoff_forces_post_2025_dynamic_search():
    trigger = SearchTrigger()
    assert trigger.determine("2024年明日方舟联动有哪些", allow_implicit=False).should_search is False
    decision = trigger.determine("2026年明日方舟联动有哪些", allow_implicit=False)
    assert decision.should_search and decision.reason == "knowledge_cutoff"


def test_lifestyle_schedule_with_date_words_does_not_force_search():
    decision = SearchTrigger().determine(
        "早上起得早，四点出头就醒了，明天把今天的份补上吧",
        allow_implicit=True,
        today=dt.date(2026, 8, 22),
    )
    assert decision.should_search is False
    assert SearchTrigger().determine("明天看电影", allow_implicit=True, today=dt.date(2026, 8, 22)).should_search is False


def test_casual_correction_with_current_word_does_not_search():
    decision = SearchTrigger().determine("现在夏天呢姐姐", allow_implicit=True)
    assert decision.should_search is False


def test_casual_correction_does_not_search_from_image_context(agent):
    fake = FakeSearch()
    agent.search = fake
    agent._history.append({"role": "assistant", "content": "图片识别结果：faceIndex"})
    agent.handle("现在夏天呢姐姐")
    assert fake.calls == 0


def test_agent_persists_only_visible_final_answer_when_llm_returns_thinking(agent):
    raw = (
        "思考过程：\n\n"
        "1. **分析输入**：用户今天很累。\n\n"
        "2. **角色扮演定位**：应该关心用户。\n\n"
        "6. **最终调整**：\n"
        "早点睡，明天再补高数吧。\n\n"
        "早点睡，明天再补高数吧。"
    )
    agent.llm.chat = lambda messages, **kwargs: raw
    result = agent.handle("今天没力气，明天把高数补上吧")
    assert result.reply == "早点睡，明天再补高数吧。"
    stored = agent.memory.recent_messages(limit=1)[0]["content"]
    assert stored == "早点睡，明天再补高数吧。"


def test_agent_accepts_structured_json_reply_on_im(agent):
    agent.llm.chat = lambda messages, **kwargs: '{"segments":[{"text":"晚安，快睡。","thinking":"不要泄漏"}]}'
    result = agent.handle("晚安老婆么么哒")
    assert result.reply == "晚安，快睡。"
    assert agent.memory.recent_messages(limit=1)[0]["content"] == "晚安，快睡。"


def test_semantic_locator_is_bounded_and_collects_candidates():
    class Client:
        def __init__(self):
            self.calls = []

        def search(self, query, **kwargs):
            self.calls.append(query)
            return [{
                "title": "《火蓝之心》复刻活动公告",
                "url": "https://example.com/arknights",
                "snippet": "明日方舟当前开启火蓝之心复刻活动",
            }]

    client = Client()
    result = SemanticLocator(max_queries=3, max_verify_queries=1).locate(
        "帮我找找现在明日方舟开启的活动复刻", client=client,
    )
    assert 1 <= len(client.calls) <= 4
    assert "火蓝之心" in result.evidence.candidate_entities
    assert "时间范围" in result.evidence.to_prompt()


def test_time_range_filters_old_dated_results_but_keeps_unknown_dates():
    now = __import__("datetime").datetime(2026, 8, 22, tzinfo=__import__("datetime").timezone.utc)
    pack = EvidencePack.from_results("活动", [
        {"title": "旧", "url": "https://example.com/old", "snippet": "old", "publishedDate": "2020-01-01"},
        {"title": "新", "url": "https://example.com/new", "snippet": "new", "publishedDate": "2026-08-21"},
        {"title": "未知日期", "url": "https://example.com/unknown", "snippet": "unknown"},
    ], now=now, time_range=("now-3d", "now+1d"))
    assert [item.title for item in pack.results] == ["新", "未知日期"]
    assert "发布日期未核实" in pack.to_prompt()
    assert "不要朗读 URL" in pack.to_prompt(channel="tts")
    assert "不要朗读 URL" not in pack.to_prompt(channel="im")


def test_search_enabled_from_config(agent):
    assert agent.search_enabled
    assert not SearchTrigger().determine("帮我查一下天气", allow_explicit=False).should_search


def test_agent_explicit_search_flow(agent):
    """显式搜索 → SearXNG → 证据进入主 LLM prompt。"""
    fake = FakeSearch()
    agent.search = fake
    r = agent.handle("帮我查一下北京天气")
    assert fake.calls == 1  # 恰好一次搜索
    assert "我查到了" in r.reply
    assert "使用规则：" not in r.reply
    assert any("本轮外部信息" in str(m.get("content")) for m in agent.llm.messages if m["role"] == "system")


def test_agent_implicit_search_is_configured_not_forced(agent):
    fake = FakeSearch()
    agent.search = fake
    agent.search_config["allow_implicit_freshness_search"] = True
    agent.handle("绝区零最近更新了什么")
    assert fake.calls == 1


def test_agent_unknown_entity_fallback_flow(agent):
    fake = FakeSearch()
    agent.search = fake
    agent.search_config["allow_implicit_freshness_search"] = True
    agent.handle("一个叫《星痕共鸣》的游戏是什么？")
    assert fake.calls == 1


def test_agent_semantic_locator_flow_is_opt_in(agent):
    fake = FakeSearch([{
        "title": "《火蓝之心》复刻活动公告",
        "url": "https://example.com/arknights",
        "snippet": "明日方舟当前开启火蓝之心复刻活动",
    }])
    agent.search = fake
    agent.search_config.update({
        "allow_implicit_freshness_search": True,
        "semantic_locator_enabled": True,
    })
    agent.handle("帮我找找现在明日方舟开启的活动复刻")
    assert 1 <= fake.calls <= 4
    assert any("时间范围" in str(m.get("content")) for m in agent.llm.messages if m["role"] == "system")


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


def test_evidence_pack_marks_quality_and_conflict():
    pack = EvidencePack.from_results("更新", [
        {"title": "官方补丁公告", "url": "https://example.com/a", "snippet": "已修复卡顿", "engine": "baidu"},
        {"title": "玩家讨论", "url": "https://forum.example/b", "snippet": "有人说没有修复", "engine": "baidu"},
    ])
    prompt = pack.to_prompt()
    assert "可信度" in prompt
    assert "不同说法" in prompt


def test_search_client_cache_avoids_duplicate_request():
    client = SearXNGClient(cache_ttl=900)
    calls = []

    class Response:
        content = b'{"results": [{"title": "T", "url": "https://example.com", "content": "S"}]}'
        def raise_for_status(self): pass
        def json(self): return {"results": [{"title": "T", "url": "https://example.com", "content": "S"}]}

    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): calls.append(1); return Response()

    import veranima.tools.search as search_module
    original = search_module.httpx.Client
    search_module.httpx.Client = lambda **kwargs: Client()
    try:
        client.search("同一个主题")
        client.search("同一个主题")
    finally:
        search_module.httpx.Client = original
    assert len(calls) == 1


def test_search_healthcheck_uses_bounded_probe(monkeypatch):
    client = SearXNGClient()
    class Response:
        status_code = 200
        def json(self): return {"results": []}
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    import veranima.tools.search as search_module
    monkeypatch.setattr(search_module.httpx, "Client", lambda **kwargs: Client())
    assert client.healthcheck() is True


def test_search_quality_sort_happens_before_result_limit():
    client = SearXNGClient(max_results=1)
    raw = {
        "results": [
            {"title": "论坛传言", "url": "https://forum.example/a", "content": "有人说", "publishedDate": "2026-08-22"},
            {"title": "官方公告", "url": "https://mihoyo.com/b", "content": "官方确认", "publishedDate": "2026-08-20"},
        ]
    }

    class Response:
        content = b"{}"
        def raise_for_status(self): pass
        def json(self): return raw

    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()

    import veranima.tools.search as search_module
    original = search_module.httpx.Client
    search_module.httpx.Client = lambda **kwargs: Client()
    try:
        result = client.search("质量排序", force_refresh=True)
    finally:
        search_module.httpx.Client = original
    assert result[0]["title"] == "官方公告"


def test_search_prefers_distinct_domains_before_duplicate_sources():
    client = SearXNGClient(max_results=2)
    raw = {"results": [
        {"title": "官方一", "url": "https://mihoyo.com/a", "content": "a"},
        {"title": "官方二", "url": "https://mihoyo.com/b", "content": "b"},
        {"title": "媒体", "url": "https://news.example/c", "content": "c"},
    ]}
    class Response:
        content = b"{}"
        def raise_for_status(self): pass
        def json(self): return raw
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    import veranima.tools.search as search_module
    original = search_module.httpx.Client
    search_module.httpx.Client = lambda **kwargs: Client()
    try:
        result = client.search("来源多样性", force_refresh=True)
    finally:
        search_module.httpx.Client = original
    assert len({item["domain"] for item in result}) == 2


def test_search_drops_backend_noise_unrelated_to_distinctive_entity():
    client = SearXNGClient()
    raw = {"results": [
        {"title": "MAC地址查询", "url": "https://example.com/mac", "content": "MAC address"},
        {"title": "明日方舟联动公告", "url": "https://example.com/ark", "content": "明日方舟联动合作活动"},
    ]}
    class Response:
        content = b"{}"
        def raise_for_status(self): pass
        def json(self): return raw
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    import veranima.tools.search as search_module
    original = search_module.httpx.Client
    search_module.httpx.Client = lambda **kwargs: Client()
    try:
        result = client.search("明日方舟 联动 2026年", force_refresh=True)
    finally:
        search_module.httpx.Client = original
    assert [item["title"] for item in result] == ["明日方舟联动公告"]


def test_page_fetch_is_bounded_and_extracts_visible_text(monkeypatch):
    client = SearXNGClient(fetch_pages=True, page_char_limit=40, max_page_results=1)
    calls = []
    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        def iter_bytes(self): return iter([b"<html><script>secret()</script><body>Hello <b>world</b></body></html>"])
    class Stream:
        def __enter__(self): return Response()
        def __exit__(self, *args): pass
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, *args, **kwargs):
            calls.append((args, kwargs))
            return Stream()
    import veranima.tools.search as search_module
    monkeypatch.setattr(search_module.httpx, "Client", lambda **kwargs: Client())
    monkeypatch.setattr(search_module.socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))])
    assert client.fetch_page("https://example.com/page") == "Hello world"
    assert calls[0][0][1].startswith("https://93.184.216.34:443/")
    assert calls[0][1]["headers"]["Host"] == "example.com"
    assert calls[0][1]["extensions"]["sni_hostname"] == "example.com"
    assert client.fetch_page("http://127.0.0.1/admin") == ""
    assert client.fetch_page("https://user:password@example.com/page") == ""
    assert "不可信数据" in EvidencePack.from_results(
        "页面", [{"title": "t", "url": "https://example.com", "snippet": "s"}]
    ).to_prompt()


def test_page_enrichment_limits_attempts_even_when_fetches_fail(monkeypatch):
    client = SearXNGClient(fetch_pages=True, max_page_results=2)
    calls = []
    monkeypatch.setattr(client, "fetch_page", lambda url: calls.append(url) or "")
    client._enrich_pages([
        {"url": "https://a.example", "snippet": "a"},
        {"url": "https://b.example", "snippet": "b"},
        {"url": "https://c.example", "snippet": "c"},
    ])
    assert calls == ["https://a.example", "https://b.example"]

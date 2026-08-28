"""BochaClient 行为测试：freshness 映射、Bing 兼容响应解析、dict 契约、鉴权失败降级。"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from veranima.tools.bocha import BochaClient, freshness_for
from veranima.tools.search import TimeRange


def test_freshness_mapping():
    now = datetime.now(timezone.utc)
    assert freshness_for(None, now) == "noLimit"
    assert freshness_for(TimeRange(now.date() - timedelta(days=1), None), now) == "oneDay"
    assert freshness_for(TimeRange(now.date() - timedelta(days=5), None), now) == "oneWeek"
    assert freshness_for(TimeRange(now.date() - timedelta(days=20), None), now) == "oneMonth"
    assert freshness_for(TimeRange(now.date() - timedelta(days=200), None), now) == "oneYear"
    assert freshness_for(TimeRange(now.date() - timedelta(days=900), None), now) == "noLimit"
    # ISO 字符串元组路径（agent 实际传的形态）
    start = (now - timedelta(days=20)).date().isoformat()
    assert freshness_for((start, now.date().isoformat()), now) == "oneMonth"


def _bing_response(request):
    body = __import__("json").loads(request.content)
    assert body["query"]
    assert "Authorization" in request.headers  # 无 Bearer 也算断言过头，只断存在
    return httpx.Response(200, json={"_type": "SearchResponse", "data": {"webPages": {"value": [
        {"name": "结果一", "url": "https://a.example/x", "displayUrl": "a.example",
         "snippet": "短摘要", "summary": "这是博查的长摘要内容", "dateLastCrawled": "2026-08-01T00:00:00Z"},
        {"name": "结果二", "url": "https://b.example/y", "displayUrl": "b.example",
         "snippet": "第二条摘要也够长可以的", "dateLastCrawled": "2026-08-02T00:00:00Z"},
        {"name": "缺摘要", "url": "https://c.example/z", "snippet": ""},  # 应被丢
    ]}}})


def test_search_parses_and_maps_fields(monkeypatch):
    c = BochaClient(api_key="sk-test", max_results=5)
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = __import__("json").loads(request.content)
        return _bing_response(request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(**kw):
        kw["transport"] = transport
        return real_client(**kw)

    monkeypatch.setattr(httpx, "Client", fake_client)
    results = c.search("衣服 质量 评价", time_range=("2026-08-01", "2026-08-28"))

    assert captured["url"] == "https://api.bochaai.com/v1/web-search"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["summary"] is True
    # ISO 日期元组 start=2026-08-01 距今 28 天 → oneMonth（不被 _coerce 降级为 oneDay）
    assert captured["body"]["freshness"] == "oneMonth"
    assert len(results) == 2  # 缺摘要的被丢
    r0 = results[0]
    # 与 SearXNGClient 相同的 dict 契约
    assert set(r0) == {"title", "url", "snippet", "domain", "engine", "published_at", "quality", "page_excerpt"}
    assert r0["engine"] == "bocha"
    assert any(x["snippet"] == "这是博查的长摘要内容" for x in results)  # summary 优先于 snippet


def test_error_degrades_to_empty(monkeypatch):
    c = BochaClient(api_key="bad", max_results=5)

    def handler(request):
        return httpx.Response(401, json={"error": "invalid key"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))
    assert c.search("anything") == []


def test_no_key_returns_empty():
    assert BochaClient(api_key="").search("anything") == []

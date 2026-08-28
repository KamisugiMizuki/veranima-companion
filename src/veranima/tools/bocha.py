"""博查（Bocha）Web Search API 客户端——安卓端联网搜索后端。

Windows 端继续用本地 SearXNG（search.provider: searxng）；安卓端 APK 里 provider=bocha。
两客户端对上层（agent.search/EvidencePack）暴露同一接口与同一 dict 契约，切换零连坐。

接口事实（2026-08-29 核实）：POST https://api.bochaai.com/v1/web-search
  Header: Authorization: Bearer <key>（open.bochaai.com 创建）
  Body:   {query, freshness, summary, count}
  响应兼容 Bing Search API：data.webPages.value[] = {name,url,displayUrl,siteName,
            snippet,summary,dateLastCrawled}
freshness 映射：TimeRange 起点距今 ≤1d→oneDay、≤7d→oneWeek、≤30d→oneMonth、
  ≤365d→oneYear、其余/无界→noLimit（服务端召回偏置；客户端 _within_time_range 仍精筛）。
summary=true 的长摘要代替 fetch_pages 抓页富化（省流量与延迟）。
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from urllib.parse import urlsplit

import httpx

from .search import TimeRange, _coerce_time_range, _diversify_results, _published_sort_key, _relevant_to_query, sanitize_search_query

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.bochaai.com/v1/web-search"


def freshness_for(time_range, reference=None) -> str:
    """TimeRange（或 (ISO起, ISO止) 元组）→ 博查 freshness 枚举。

    不复用 _coerce_time_range：它把 ISO 日期元组降级成 today（SearXNG 端靠客户端精筛无碍），
    博查端会让服务端召回被 oneDay 错限。
    """
    if isinstance(time_range, TimeRange):
        start = time_range.start
    elif isinstance(time_range, tuple) and len(time_range) == 2:
        try:
            start = dt.date.fromisoformat(str(time_range[0]))
        except ValueError:
            start = _coerce_time_range(time_range, reference).start if time_range[0] else None
    else:
        start = None
    if not start:
        return "noLimit"
    days = (dt.datetime.now(dt.timezone.utc).date() - start).days
    if days <= 1:
        return "oneDay"
    if days <= 7:
        return "oneWeek"
    if days <= 30:
        return "oneMonth"
    if days <= 365:
        return "oneYear"
    return "noLimit"


class BochaClient:
    """与 SearXNGClient 同接口：search(query, time_range=…) → list[dict]；healthcheck()。"""

    def __init__(self, api_key: str, *, max_results: int = 5, timeout: float = 8.0,
                 cache_ttl: float = 900, base_url: str | None = None):
        self.api_key = api_key.strip()
        self.max_results = max(1, min(int(max_results), 10))
        self.timeout = max(0.5, float(timeout))
        self.cache_ttl = max(0.0, float(cache_ttl))
        # config 里 base_url = 完整 endpoint（含 /v1/web-search），非根地址
        self.base_url = (base_url or ENDPOINT).rstrip("/")
        self._cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

    def search(self, query: str, *, language: str = "zh-CN", force_refresh: bool = False,
               time_range: TimeRange | tuple[str, str] | None = None) -> list[dict]:
        query = sanitize_search_query(query)
        if not query or not self.api_key:
            return []
        cache_key = (query.strip().casefold(), repr(time_range))
        cached = self._cache.get(cache_key)
        if cached and not force_refresh and time.monotonic() - cached[0] < self.cache_ttl:
            return [dict(item) for item in cached[1]]
        payload = {"query": query[:240], "count": self.max_results * 2,
                   "summary": True, "freshness": freshness_for(time_range)}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(self.base_url, json=payload,
                                   headers={"Authorization": f"Bearer {self.api_key}"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("bocha search failed: %s", type(exc).__name__)
            return []
        pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        out: list[dict] = []
        seen: set[str] = set()
        for raw in pages:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("name") or "").strip()[:120]
            url = str(raw.get("url") or "").strip()
            snippet = str(raw.get("summary") or raw.get("snippet") or "").strip()[:600]
            published = str(raw.get("dateLastCrawled") or "").strip()[:32]
            if not title or not url.lower().startswith(("http://", "https://")) or not snippet:
                continue
            if not _relevant_to_query(query, _Shim(title, url, snippet)):
                continue
            if url in seen:
                continue
            seen.add(url)
            domain = str(raw.get("displayUrl") or urlsplit(url).netloc).lower()
            out.append({"title": title, "url": url, "snippet": snippet,
                        "domain": domain, "engine": "bocha",
                        "published_at": published or None, "quality": "medium",
                        "page_excerpt": ""})
        if out:
            out.sort(key=lambda item: _published_sort_key(item.get("published_at")))
            out = _diversify_results(out, self.max_results)
            self._cache[cache_key] = (time.monotonic(), out)
        return out

    def healthcheck(self) -> bool:
        """真探活：一次最小查询（固定词，不含用户内容）。"""
        try:
            with httpx.Client(timeout=min(self.timeout, 5.0)) as client:
                resp = client.post(self.base_url, json={"query": "test", "count": 1},
                                   headers={"Authorization": f"Bearer {self.api_key}"})
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("bocha healthcheck failed: %s", type(exc).__name__)
            return False


class _Shim:
    __slots__ = ("title", "url", "snippet")

    def __init__(self, title, url, snippet):
        self.title, self.url, self.snippet = title, url, snippet

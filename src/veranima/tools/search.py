"""Veranima 的最小联网搜索链：显式触发、SearXNG 清洗、临时证据注入。"""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchDecision:
    should_search: bool
    reason: str
    user_requested: bool = False
    query: str = ""
    force_refresh: bool = False


class SearchTrigger:
    """显式搜索 + 低成本时效词判断；不调用 LLM 做分类。"""

    _request_words = ("帮我查", "查一下", "查查", "搜一下", "搜搜", "搜索", "查最新", "看看最近")
    _disable_words = ("别联网", "不要联网", "不用联网", "不要搜索", "别搜索", "不用查")
    _freshness_words = ("最近", "今天", "昨天", "刚刚", "这周", "目前", "现在", "最新", "新出的", "更新", "上线", "发布", "风评", "后续")
    _stable_patterns = ("是哪年", "什么时候发行", "什么是", "怎么用", "是什么意思")
    _current_fact_patterns = ("哪一年发布", "哪年发布", "什么时候发布", "何时发布", "发布日期", "发行日期", "发售日", "上市时间", "上线时间")

    def determine(self, text: str, *, allow_implicit: bool = False) -> SearchDecision:
        text = (text or "").strip()
        if any(word in text for word in self._disable_words):
            return SearchDecision(False, "privacy_blocked")
        explicit = any(word in text for word in self._request_words)
        force_refresh = any(word in text for word in ("再查", "重新查", "刷新一下", "强制刷新"))
        is_current_fact = any(pattern in text for pattern in self._current_fact_patterns)
        implicit = allow_implicit and (any(word in text for word in self._freshness_words) or is_current_fact)
        if not explicit and not implicit:
            return SearchDecision(False, "no_explicit_request")
        if implicit and not explicit and any(pattern in text for pattern in self._stable_patterns) and not is_current_fact:
            return SearchDecision(False, "stable_knowledge")
        query = text
        for word in self._request_words:
            query = query.replace(word, " ")
        query = re.sub(r"[，。！？：:、]+", " ", query).strip()
        if not query:
            return SearchDecision(False, "empty_query", True)
        return SearchDecision(True, "explicit_request" if explicit else "freshness", explicit, query[:240], force_refresh)


@dataclass(frozen=True)
class SearchPlan:
    query: str
    max_results: int = 5
    timeout_seconds: float = 8.0
    language: str = "zh-CN"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str = ""
    engine: str = ""
    published_at: str | None = None
    quality: str = "medium"

    @classmethod
    def from_raw(cls, raw: dict) -> "SearchResult | None":
        title = _clean(raw.get("title", ""), 120)
        url = _safe_url(raw.get("url", ""))
        snippet = _clean(raw.get("content", raw.get("snippet", "")), 300)
        if not title or not url or not snippet:
            return None
        domain = urlsplit(url).netloc.lower()
        quality = "high" if any(x in domain for x in ("github.com", "microsoft.com", "mihoyo.com", "hoyoverse.com")) or "官方" in title else "low" if any(x in domain for x in ("forum", "tieba", "reddit")) else "medium"
        return cls(title, url, snippet, domain, str(raw.get("engine", "")), raw.get("publishedDate"), quality)


@dataclass(frozen=True)
class EvidencePack:
    topic: str
    searched_at: str
    results: tuple[SearchResult, ...] = field(default_factory=tuple)
    expires_minutes: int = 15

    @classmethod
    def from_results(cls, topic: str, results: list[dict], *, now: dt.datetime | None = None) -> "EvidencePack":
        seen: set[str] = set()
        clean: list[SearchResult] = []
        for raw in results:
            item = raw if isinstance(raw, SearchResult) else SearchResult.from_raw(raw)
            if item is None or item.url in seen:
                continue
            seen.add(item.url)
            clean.append(item)
            if len(clean) >= 5:
                break
        stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
        return cls(topic[:160], stamp, tuple(clean))

    def to_prompt(self) -> str:
        lines = [
            "【本轮外部信息，仅供核对】",
            f"检索时间：{self.searched_at}",
            f"主题：{self.topic}",
        ]
        if not self.results:
            lines.append("没有返回结果；没有找到可用的近期外部信息，不要补写或猜测搜索结果。")
        else:
            for i, item in enumerate(self.results[:3], 1):
                lines.append(f"{i}. [可信度：{item.quality}] {item.title}：{item.snippet}（来源：{item.url}）")
            if self._has_conflict():
                lines.append("提示：来源之间存在不同说法，不要强行裁决；需要时列出各自来源。")
        lines.extend([
            "使用规则：只能使用上述证据支持的内容；证据不足时明确说不确定。",
            "不得把搜索结果说成亲身经历或长期记忆；这是临时上下文，不要写入长期记忆。",
            "用户要求来源时，可以返回对应标题和 URL。",
        ])
        return "\n".join(lines)

    def _has_conflict(self) -> bool:
        positive = ("已修复", "已上线", "支持", "通过")
        negative = ("未修复", "没有修复", "不支持", "失败", "尚未")
        joined = " ".join(item.snippet for item in self.results)
        return any(x in joined for x in positive) and any(x in joined for x in negative)


class SearXNGClient:
    """SearXNG JSON API 客户端；失败返回空结果，不阻塞聊天。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", max_results: int = 5,
                 timeout: float = 8.0, max_response_bytes: int = 1_048_576, cache_ttl: float = 900):
        self.base_url = base_url.rstrip("/")
        self.max_results = max(1, min(int(max_results), 5))
        self.timeout = max(0.5, float(timeout))
        self.max_response_bytes = max_response_bytes
        self.cache_ttl = max(0.0, float(cache_ttl))
        self._cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

    def search(self, query: str, *, language: str = "zh-CN", force_refresh: bool = False) -> list[dict]:
        cache_key = (query.strip().casefold(), language)
        cached = self._cache.get(cache_key)
        if cached and not force_refresh and time.monotonic() - cached[0] < self.cache_ttl:
            return [dict(item) for item in cached[1]]
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/search", params={"q": query[:240], "format": "json", "language": language})
                resp.raise_for_status()
                if len(resp.content) > self.max_response_bytes:
                    logger.warning("search response too large")
                    return []
                data = resp.json()
        except Exception as exc:
            logger.warning("search failed: %s", exc)
            return []
        out: list[dict] = []
        seen_titles: set[str] = set()
        for raw in data.get("results", []) if isinstance(data, dict) else []:
            item = SearchResult.from_raw(raw if isinstance(raw, dict) else {})
            if item is None:
                continue
            title_key = re.sub(r"\W+", "", item.title.casefold())
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            out.append({"title": item.title, "url": item.url, "snippet": item.snippet,
                        "domain": item.domain, "engine": item.engine, "published_at": item.published_at})
            if len(out) >= self.max_results:
                break
        self._cache[cache_key] = (time.monotonic(), out)
        return out

    def format_results(self, results: list[dict]) -> str:
        """兼容旧调用方；新 Agent 使用 EvidencePack.to_prompt。"""
        return EvidencePack.from_results("搜索结果", results).to_prompt()


def _clean(value: object, limit: int) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _safe_url(value: object) -> str:
    try:
        parts = urlsplit(str(value or ""))
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return ""
        try:
            address = ipaddress.ip_address(parts.hostname or "")
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                return ""
        except ValueError:
            pass
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid"))]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except ValueError:
        return ""

"""Veranima 的最小联网搜索链：显式触发、SearXNG 清洗、临时证据注入。"""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import logging
import re
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


class SearchTrigger:
    """Phase 1 只做显式搜索；隐式时效触发留给后续阶段。"""

    _request_words = ("帮我查", "查一下", "查查", "搜一下", "搜搜", "搜索", "查最新", "看看最近")
    _disable_words = ("别联网", "不要联网", "不用联网", "不要搜索", "别搜索", "不用查")

    def determine(self, text: str) -> SearchDecision:
        text = (text or "").strip()
        if any(word in text for word in self._disable_words):
            return SearchDecision(False, "privacy_blocked")
        if not any(word in text for word in self._request_words):
            return SearchDecision(False, "no_explicit_request")
        query = text
        for word in self._request_words:
            query = query.replace(word, " ")
        query = re.sub(r"[，。！？：:、]+", " ", query).strip()
        if not query:
            return SearchDecision(False, "empty_query", True)
        return SearchDecision(True, "explicit_request", True, query[:240])


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

    @classmethod
    def from_raw(cls, raw: dict) -> "SearchResult | None":
        title = _clean(raw.get("title", ""), 120)
        url = _safe_url(raw.get("url", ""))
        snippet = _clean(raw.get("content", raw.get("snippet", "")), 300)
        if not title or not url or not snippet:
            return None
        return cls(title, url, snippet, urlsplit(url).netloc, str(raw.get("engine", "")), raw.get("publishedDate"))


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
                lines.append(f"{i}. {item.title}：{item.snippet}（来源：{item.url}）")
        lines.extend([
            "使用规则：只能使用上述证据支持的内容；证据不足时明确说不确定。",
            "不得把搜索结果说成亲身经历或长期记忆；这是临时上下文，不要写入长期记忆。",
            "用户要求来源时，可以返回对应标题和 URL。",
        ])
        return "\n".join(lines)


class SearXNGClient:
    """SearXNG JSON API 客户端；失败返回空结果，不阻塞聊天。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", max_results: int = 5,
                 timeout: float = 8.0, max_response_bytes: int = 1_048_576):
        self.base_url = base_url.rstrip("/")
        self.max_results = max(1, min(int(max_results), 5))
        self.timeout = max(0.5, float(timeout))
        self.max_response_bytes = max_response_bytes

    def search(self, query: str, *, language: str = "zh-CN") -> list[dict]:
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
            key = re.sub(r"\W+", "", item.title.casefold())
            if key in seen_titles:
                continue
            seen_titles.add(key)
            out.append({"title": item.title, "url": item.url, "snippet": item.snippet,
                        "domain": item.domain, "engine": item.engine, "published_at": item.published_at})
            if len(out) >= self.max_results:
                break
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

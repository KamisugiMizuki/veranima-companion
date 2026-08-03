"""联网搜索工具（DESIGN.md 8.5 节，方案 A：LLM 工具调用）。

SearXNG 本地服务（127.0.0.1:8080）→ 搜索引擎结果，注入对话。
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "搜索互联网获取外部信息（实时新闻、天气、事实确认、用户提到的陌生事物）。"
            "仅当对话确实需要外部信息时调用；日常陪伴闲聊不要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词（简洁中文）"}},
            "required": ["query"],
        },
    },
}


class SearXNGClient:
    """SearXNG JSON API 客户端。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", max_results: int = 4, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.max_results = max_results
        self.timeout = timeout

    def search(self, query: str) -> list[dict]:
        """搜索并返回 [{title, url, snippet}]，失败返回空列表。"""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    f"{self.base_url}/search",
                    params={"q": query, "format": "json"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("search failed: %s", e)
            return []
        results = []
        for r in data.get("results", [])[: self.max_results]:
            results.append({
                "title": r.get("title", "")[:60],
                "url": r.get("url", ""),
                "snippet": r.get("content", r.get("snippet", ""))[:120],
            })
        return results

    def format_results(self, results: list[dict]) -> str:
        """结果 → 注入文本（model 作为工具结果读取）。"""
        if not results:
            return "搜索没有返回结果。"
        lines = ["搜索结果（来自搜索引擎，供参考）："]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']} — {r['snippet']}（{r['url']}）")
        return "\n".join(lines)

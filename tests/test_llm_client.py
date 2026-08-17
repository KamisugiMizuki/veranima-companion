"""LLM 客户端错误分类测试：400 jinja 模板错误 ≠ 模型未加载。

回归 2026-08-04：llama.cpp 模板拒绝消息序列时返回 400（错误体含 jinja），
但 client 之前把 400 一律归为"模型未加载"，导致模型明明加载着却回复
"（我好像还没醒过来……）"的误导性兜底。
"""

from __future__ import annotations

import httpx
import pytest

from veranima.llm.client import LLMClient, LLMError, LLMUnavailableError


class _FakeTransport:
    """模拟 httpx.Client：post 返回指定状态码+文本（>=400 抛 HTTPStatusError）。"""

    def __init__(self, status: int, text: str):
        self._status = status
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
        resp = httpx.Response(self._status, text=self._text, request=req)
        if self._status >= 400:
            raise httpx.HTTPStatusError(f"{self._status} error", request=req, response=resp)
        return resp


@pytest.fixture
def client():
    return LLMClient({"base_url": "https://api.example.com/v1", "model": "qwen3-8b"})


def _stub_http(monkeypatch, status: int, text: str) -> None:
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeTransport(status, text))


def test_400_jinja_template_error_is_llm_error(client, monkeypatch):
    """400 + jinja 模板错误：请求内容问题 → LLMError（不是模型未加载）。"""
    _stub_http(monkeypatch, 400,
               '{"error":"Error rendering prompt with jinja template: \\"No user query found in messages\\"."}')
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


def test_400_plain_model_not_loaded_is_unavailable(client, monkeypatch):
    """400 + 非模板错误（如模型未加载）：仍归 LLMUnavailableError。"""
    _stub_http(monkeypatch, 400, '{"error":"model not loaded"}')
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])


def test_404_is_unavailable(client, monkeypatch):
    """404（模型未加载典型响应）：LLMUnavailableError。"""
    _stub_http(monkeypatch, 404, "not found")
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])


def test_422_is_unavailable(client, monkeypatch):
    """422：LLMUnavailableError。"""
    _stub_http(monkeypatch, 422, "unprocessable")
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])

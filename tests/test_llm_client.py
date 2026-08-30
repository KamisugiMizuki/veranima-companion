"""LLM 客户端错误分类与结构化输出测试。"""
from __future__ import annotations

import httpx
import pytest

from veranima.llm.client import LLMClient, LLMError, LLMTimeoutError, LLMUnavailableError


class _FakeTransport:
    """模拟 httpx.Client：post 返回指定状态码+文本。"""

    def __init__(self, status: int, text: str):
        self._status = status
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        self.last_payload = kwargs.get("json") or {}
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


def _stub_http_capture(monkeypatch, captured: dict) -> None:
    """200 响应 + 记录请求 payload（验 model 字段用）。"""
    class T:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, *a, **kw):
            captured.update(kw.get("json") or {})
            req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"},
                                                          "finish_reason": "stop"}]}, request=req)
    monkeypatch.setattr(httpx, "Client", lambda **kw: T())


def test_vision_model_switch_only_for_image_messages():
    cfg = {"base_url": "https://api.example.com/v1", "model": "flash",
           "vision_model": "flash-vision-exp"}
    c = LLMClient(cfg)
    assert c._model_for([{"role": "user", "content": "hi"}]) == "flash"
    assert c._model_for([{"role": "user", "content": [
        {"type": "text", "text": "看"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]}]) == "flash-vision-exp"
    # 未配置 vision_model → 永不切换
    plain = LLMClient(cfg | {"vision_model": ""})
    assert plain._model_for([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "x"}}]}]) == "flash"


def test_vision_model_reaches_request_payload(monkeypatch):
    cap: dict = {}
    _stub_http_capture(monkeypatch, cap)
    c = LLMClient({"base_url": "https://api.example.com/v1", "model": "flash",
                   "vision_model": "flash-vision-exp"})
    c.chat([{"role": "user", "content": [
        {"type": "text", "text": "这是什么"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]}])
    assert cap["model"] == "flash-vision-exp"
    cap2: dict = {}
    _stub_http_capture(monkeypatch, cap2)
    c.chat([{"role": "user", "content": "纯文本"}])
    assert cap2["model"] == "flash"


def test_400_jinja_template_error_is_llm_error(client, monkeypatch):
    _stub_http(monkeypatch, 400,
               '{"error":"Error rendering prompt with jinja template: \\"No user query found in messages\\"."}')
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


def test_400_plain_model_not_loaded_is_unavailable(client, monkeypatch):
    _stub_http(monkeypatch, 400, '{"error":"model not loaded"}')
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])


def test_404_is_unavailable(client, monkeypatch):
    _stub_http(monkeypatch, 404, "not found")
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])


def test_422_is_unavailable(client, monkeypatch):
    _stub_http(monkeypatch, 422, "unprocessable")
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "hi"}])


def test_read_timeout_is_not_model_unavailable(client, monkeypatch):
    class Transport:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(httpx, "Client", lambda **kw: Transport())
    with pytest.raises(LLMTimeoutError):
        client.chat([{"role": "user", "content": "hi"}])


def test_timeout_retries_three_times_then_returns_fourth_response(monkeypatch):
    calls = []
    client_timeouts = []

    class Transport:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, url, **kwargs):
            calls.append(kwargs["json"])
            if len(calls) <= 3:
                raise httpx.ReadTimeout("read timed out")
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "第四次成功"}}]},
                request=req,
            )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: (client_timeouts.append(kw["timeout"]) or Transport()),
    )
    client = LLMClient({
        "base_url": "https://api.example.com/v1",
        "model": "qwen3-8b",
        "timeout": 30,
        "timeout_retries": 3,
    })
    assert client.chat([{"role": "user", "content": "hi"}]) == "第四次成功"
    assert len(calls) == 4
    assert client_timeouts == [30.0, 30.0, 30.0, 30.0]


def test_timeout_retries_three_times_then_raises_after_four_attempts(monkeypatch):
    calls = []
    client_timeouts = []

    class Transport:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, *args, **kwargs):
            calls.append(1)
            raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: (client_timeouts.append(kw["timeout"]) or Transport()),
    )
    client = LLMClient({
        "base_url": "https://api.example.com/v1",
        "model": "qwen3-8b",
        "timeout": 30,
        "timeout_retries": 3,
    })
    with pytest.raises(LLMTimeoutError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 4
    assert client_timeouts == [30.0, 30.0, 30.0, 30.0]


def test_chat_structured_requests_json_object(monkeypatch):
    calls = []

    class Transport:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, url, **kwargs):
            calls.append(kwargs["json"])
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"segments":[]}'}}]}, request=req)

    monkeypatch.setattr(httpx, "Client", lambda **kw: Transport())
    client = LLMClient({"base_url": "https://api.example.com/v1", "model": "qwen3-8b"})
    client.chat_structured([{"role": "user", "content": "hi"}])
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_chat_structured_falls_back_when_provider_rejects_json(monkeypatch):
    calls = []

    class Transport:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, url, **kwargs):
            calls.append(kwargs["json"])
            req = httpx.Request("POST", url)
            if "response_format" in kwargs["json"]:
                resp = httpx.Response(400, text='{"error":"response_format json_object unsupported"}', request=req)
                raise httpx.HTTPStatusError("400 error", request=req, response=resp)
            return httpx.Response(200, json={"choices": [{"message": {"content": "plain fallback"}}]}, request=req)

    monkeypatch.setattr(httpx, "Client", lambda **kw: Transport())
    client = LLMClient({"base_url": "https://api.example.com/v1", "model": "qwen3-8b"})
    assert client.chat_structured([{"role": "user", "content": "hi"}]) == "plain fallback"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_chat_structured_accepts_plain_content_for_text_fallback(monkeypatch):
    class Transport:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, url, **kwargs):
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"choices": [{"message": {"content": "plain fallback"}}]}, request=req)

    monkeypatch.setattr(httpx, "Client", lambda **kw: Transport())
    client = LLMClient({"base_url": "https://api.example.com/v1", "model": "qwen3-8b"})
    assert client.chat_structured([{"role": "user", "content": "hi"}], max_tokens=256) == "plain fallback"


# ---------- 2026-08-31 截断治理（残句事件回归） ----------

def _stub_finish(monkeypatch, captured, finish_reason, content="半截"):
    class T:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def post(self, *a, **kw):
            captured.update(kw.get("json") or {})
            req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
            return httpx.Response(200, json={"choices": [{
                "message": {"content": content}, "finish_reason": finish_reason}]}, request=req)
    monkeypatch.setattr(httpx, "Client", lambda **kw: T())


def test_truncated_completion_raises_not_returns_fragment(client, monkeypatch):
    from veranima.llm.client import LLMTruncatedError
    _stub_finish(monkeypatch, {}, "length")
    with pytest.raises(LLMTruncatedError):
        client.chat_raw([{"role": "user", "content": "hi"}], max_tokens=120)


def test_content_filter_also_treated_as_truncation(client, monkeypatch):
    from veranima.llm.client import LLMTruncatedError
    _stub_finish(monkeypatch, {}, "content_filter")
    with pytest.raises(LLMTruncatedError):
        client.chat_raw([{"role": "user", "content": "hi"}])


def test_chat_budget_floor_applied(client, monkeypatch):
    """小 max_tokens 抬到 short_task_max_tokens；None 不抬（走全局 max_tokens）。"""
    cap = {}
    _stub_finish(monkeypatch, cap, "stop", content="好")
    client.config["short_task_max_tokens"] = 512
    client.chat([{"role": "user", "content": "hi"}], max_tokens=120)
    assert cap["max_tokens"] == 512
    client.chat([{"role": "user", "content": "hi"}], max_tokens=2048)
    assert cap["max_tokens"] == 2048
    cap.clear()
    client.chat([{"role": "user", "content": "hi"}])
    assert cap["max_tokens"] == client.max_tokens  # 未显式传=默认全局


def test_observe_image_budget_floor(monkeypatch):
    c = LLMClient({"base_url": "https://api.example.com/v1", "model": "v",
                   "short_task_max_tokens": 640})
    cap = {}
    _stub_finish(monkeypatch, cap, "stop", content="{}")
    c.observe_image("ZmFrZQ==")
    assert cap["max_tokens"] == 640  # 原硬编码 200 已抬

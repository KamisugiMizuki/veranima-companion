"""LLM 客户端：OpenAI 兼容接口（httpx 直调远程 API）。

配置 base_url/model/api_key 即可对接任意 OpenAI 兼容服务（DeepSeek/通义/硅基流动等）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _split_sentences(text: str) -> list[str]:
    """按句切分（DESIGN 4.13 分片粒度：。！？…断句），保留标点。"""
    import re
    parts = re.split(r"(?<=[。！？…])", text)
    return [p.strip() for p in parts if p.strip()]


class LLMUnavailableError(RuntimeError):
    """LLM 服务不可用：连接失败 / 鉴权失败。"""


class LLMError(RuntimeError):
    """LLM 服务在线但生成失败。"""


class LLMTimeoutError(LLMError):
    """LLM 请求超时：服务状态未知，不等同于模型未启动。"""


class LLMTruncatedError(LLMError):
    """输出被 max_tokens 腰斩（finish_reason=length/content_filter）。

    2026-08-31 实锤教训：截断的半截 JSON 会被容错解析器抠出残句直发
    （MuMu 上「……您晚饭，吃」）。调用方把此异常与超时同等对待（重试/
    回退），而非消费残破内容——半句话不是话。
    """


class LLMClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = config.get("base_url", "")
        self.model = config.get("model", "qwen3:8b")
        self.api_key = config.get("api_key", "")
        self.temperature = config.get("temperature", 0.8)
        self.max_tokens = config.get("max_tokens", 1024)
        self.low_energy_max_tokens = config.get("low_energy_max_tokens", 256)
        self._timeout = float(config.get("timeout", 30.0))
        self._timeout_retries = max(0, int(config.get("timeout_retries", 3)))
        # 含图请求自动切的视觉模型（DeepSeek 文档：图块只在 vision 模型上可用）。
        # base_url/api_key 复用本 profile——只有模型名不同。留空=不切（当前模型自己带图会 400）。
        self.vision_model = str(config.get("vision_model") or "").strip()

    def _model_for(self, messages: list[dict]) -> str:
        if self.vision_model and any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "image_url" for b in m["content"] if isinstance(b, dict))
            for m in messages
        ):
            return self.vision_model
        return self.model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def chat(self, messages: list[dict], *, max_tokens: int | None = None, temperature: float | None = None) -> str:
        """单次对话生成。messages: [{'role','content'}, ...]

        错误分类：连接失败/模型未加载 → LLMUnavailableError；在线但生成失败 → LLMError。
        空 content 也抛 LLMError（Qwen3 thinking 模型：短任务预算可能全被 reasoning 吃掉，
        返回空串会让调用方发出空回复；统一由调用方兜底）。
        未配置 base_url：logger 提示并返回缺省提示文案（不报错，调用方可直接发出）。

        max_tokens 预算下限（2026-08-31）：小预算被 reasoning 烧空/腰斩是
        反复实锤的坑（MuMu 残句事件、R0_SPEC 6），一律抬到
        llm.short_task_max_tokens——DeepSeek 按实际生成 token 计费，
        max_tokens 只是上限，抬预算不涨价。
        """
        if not self.base_url:
            logger.info("LLM 未配置（config.yaml llm.base_url 留空）——返回缺省提示")
            return "（模型连接尚未配置：请在 config.yaml 填写 llm.base_url / llm.api_key）"
        floor = int(self.config.get("short_task_max_tokens", 512) or 512)
        if max_tokens is not None:
            max_tokens = max(int(max_tokens), floor)
        msg = self.chat_raw(messages, max_tokens=max_tokens, temperature=temperature)
        content = (msg.get("content") or "").strip()
        if not content:
            reasoning = msg.get("reasoning_content") or ""
            logger.warning("LLM returned empty content (reasoning %d chars)", len(reasoning))
            raise LLMError("empty completion: token budget consumed by thinking")
        return content

    def chat_structured(self, messages: list[dict], *, max_tokens: int | None = None,
                        temperature: float | None = None) -> str:
        """请求 JSON object；服务端拒绝 response_format 时退回普通 chat。"""
        budget = max(1024, int(max_tokens or self.max_tokens))
        try:
            msg = self.chat_raw(
                messages, max_tokens=budget, temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = (msg.get("content") or "").strip()
            if not content:
                # 推理模型偶发把预算烧在 reasoning 上返回空 content：降级普通 chat 重试
                logger.warning("structured completion empty (thinking ate budget?); retry plain")
                return self.chat(messages, max_tokens=budget, temperature=temperature)
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                logger.warning("structured response is not JSON; passing content to reply parser")
                return content
            if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
                logger.warning("structured response shape invalid; passing content to reply parser")
            return content
        except TypeError:
            return self.chat(messages, max_tokens=budget, temperature=temperature)
        except LLMError as exc:
            if "structured response unsupported" in str(exc).lower():
                logger.warning("structured output rejected; falling back to plain chat")
                return self.chat(messages, max_tokens=budget, temperature=temperature)
            raise

    def stream_chat(self, messages: list[dict], *, max_tokens: int | None = None,
                    temperature: float | None = None) -> list[str]:
        """流式对话生成（DESIGN 4.13）：按句分片返回（。！？…断句）。

        返回句子列表（完整回复按句切分）；API 不支持 stream / 流中断时回退
        一次性 chat（降级：单元素列表）。用于桌宠打字机 + TTS 逐句。
        未配置 base_url：与 chat 一致返回缺省提示单句（不报错）。
        """
        if not self.base_url:
            logger.info("LLM 未配置（config.yaml llm.base_url 留空）——流式返回缺省提示")
            return ["（模型连接尚未配置：请在 config.yaml 填写 llm.base_url / llm.api_key）"]
        payload = {
            "model": self._model_for(messages),
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": True,
        }
        chunks: list[str] = []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions",
                                   json=payload, headers=self._headers()) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            import json as _json
                            delta = _json.loads(data)["choices"][0]["delta"].get("content", "")
                        except (KeyError, IndexError, _json.JSONDecodeError):
                            continue
                        if delta:
                            chunks.append(delta)
        except httpx.TimeoutException as e:
            logger.error("LLM stream timed out: %s", e)
            raise LLMTimeoutError(str(e)) from e
        except httpx.ConnectError as e:
            logger.error("LLM stream unavailable: %s", e)
            raise LLMUnavailableError(str(e)) from e
        except Exception as e:
            # 流中断/协议异常 → 降级一次性（DESIGN 4.13 降级）
            logger.warning("stream failed (%s), falling back to one-shot", e)
            return [self.chat(messages, max_tokens=max_tokens, temperature=temperature)]

        text = "".join(chunks).strip()
        if not text:
            # 空流 → 降级一次性（避免空回复）
            logger.warning("stream empty, falling back to one-shot")
            return [self.chat(messages, max_tokens=max_tokens, temperature=temperature)]
        return _split_sentences(text)

    def observe_image(self, image_b64: str, *, prompt: str | None = None) -> str:
        """VISION_SPEC L3 大模型观察：截图 → 结构化理解。

        image_b64：无头 base64（PNG）。返回模型文本；失败返回 ""（调用方降级）。
        """
        payload = {
            "model": self.vision_model or self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or (
                        "简要描述这张屏幕截图里发生了什么（50字内）。"
                        '只输出 JSON：{"observe": "描述", "tag": "类别(游戏/办公/浏览器/其他)"}'
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }],
            # 200 预算会被推理烧空/腰斩（2026-08-31 同类坑统一）：抬到下限，上限不涨价
            "max_tokens": max(200, int(self.config.get("short_task_max_tokens", 512) or 512)),
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            return (data["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:
            logger.warning("observe_image failed: %s", e)
            return ""

    def chat_raw(self, messages: list[dict], *, max_tokens: int | None = None,
                 temperature: float | None = None, tools: list[dict] | None = None,
                 response_format: dict | None = None) -> dict:
        """对话生成（完整 message 返回，含 tool_calls）。tools 为 OpenAI 格式工具定义。"""
        payload = {
            "model": self._model_for(messages),
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if response_format:
            payload["response_format"] = response_format
        attempts = self._timeout_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
                    resp.raise_for_status()
                    data = resp.json()
                choice = data["choices"][0]
                message = choice["message"]
                finish_reason = str(choice.get("finish_reason") or "")
                if finish_reason and finish_reason != "stop":
                    logger.warning(
                        "LLM completion ended with finish_reason=%s (content_chars=%d)",
                        finish_reason,
                        len(str(message.get("content") or "")),
                    )
                if finish_reason in ("length", "content_filter", "sensitive"):
                    # 截断/审查的内容禁止外流：宁可让调用方走回退，不发半句
                    raise LLMTruncatedError(
                        f"completion truncated (finish_reason={finish_reason})")
                return message
            except LLMUnavailableError:
                raise
            except httpx.TimeoutException as e:
                if attempt < attempts:
                    logger.warning(
                        "LLM request timed out; retrying (%d/%d)",
                        attempt, attempts - 1,
                    )
                    continue
                logger.error("LLM request timed out after %d attempts: %s", attempts, e)
                raise LLMTimeoutError(str(e)) from e
            except httpx.ConnectError as e:
                logger.error("LLM unavailable: %s", e)
                raise LLMUnavailableError(str(e)) from e
            except httpx.HTTPStatusError as e:
                # 4xx/5xx：远程 API 返回 400 可能是请求内容问题（模型其实在线），
                # 401/403 是鉴权失败。400 且非鉴权类时归 LLMError 而非 Unavailable，
                # 避免误导性的"服务不可用"唤醒兜底（2026-08-04 修复）。
                body = (e.response.text or "").lower()
                structured_unsupported = bool(response_format) and (
                    "response_format" in body or "json_object" in body
                    or "structured output" in body or "json mode" in body
                )
                if structured_unsupported:
                    raise LLMError(f"structured response unsupported: {e.response.text[:200]}") from e
                is_template_error = e.response.status_code == 400 and (
                    "jinja" in body or "prompt template" in body or "template" in body
                )
                if not is_template_error and e.response.status_code in (400, 404, 422):
                    logger.error("LLM model not loaded or bad request: %s", e.response.text[:200])
                    raise LLMUnavailableError(f"model not loaded: {e.response.status_code}") from e
                logger.error("LLM server error: %s", e.response.text[:200])
                raise LLMError(str(e)) from e
            except LLMTruncatedError:
                raise  # 裸 Exception 会把它重包成父类丢掉子类型（调用方无法区分）
            except Exception as e:
                logger.error("LLM chat failed: %s", e)
                raise LLMError(str(e)) from e
        raise LLMTimeoutError("LLM request timed out")

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/models", headers=self._headers())
            return resp.status_code == 200
        except Exception:
            return False

    def is_model_loaded(self) -> bool:
        """远程 API 模式（配置了 api_key）：无「加载」概念，始终可用。

        放行交给 chat 的异常处理（连接失败/鉴权失败会在 chat_raw 分类）。
        """
        return True

    def ensure_model(self) -> bool:
        """检查远程 API 是否可达且配置的模型存在。"""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/models", headers=self._headers())
            if resp.status_code != 200:
                return False
            names = [m.get("id", "") for m in resp.json().get("data", [])]
            if self.model in names:
                return True
            logger.warning("model %s not found (available: %s). 检查 config 的 model 名。", self.model, names)
            return False
        except Exception as e:
            logger.error("cannot reach LLM server at %s: %s", self.base_url, e)
            return False

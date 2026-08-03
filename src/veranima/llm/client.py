"""LLM 客户端：OpenAI 兼容接口（httpx 直调）。

兼容 LM Studio（http://localhost:1234/v1）与 Ollama（http://localhost:11434/v1）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """LLM 服务不可用：连接失败 / 模型未加载（游戏模式 off 时典型）。"""


class LLMError(RuntimeError):
    """LLM 服务在线但生成失败。"""


class LLMClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = config.get("base_url", "http://localhost:1234/v1")
        self.model = config.get("model", "qwen3:8b")
        self.api_key = config.get("api_key", "")
        self.temperature = config.get("temperature", 0.8)
        self.max_tokens = config.get("max_tokens", 1024)
        self.low_energy_max_tokens = config.get("low_energy_max_tokens", 256)
        self._timeout = config.get("timeout", 120.0)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def chat(self, messages: list[dict], *, max_tokens: int | None = None, temperature: float | None = None) -> str:
        """单次对话生成。messages: [{'role','content'}, ...]

        错误分类：连接失败/模型未加载 → LLMUnavailableError；在线但生成失败 → LLMError。
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except LLMUnavailableError:
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.error("LLM unavailable: %s", e)
            raise LLMUnavailableError(str(e)) from e
        except httpx.HTTPStatusError as e:
            # 4xx/5xx：LM Studio 模型未加载时返回 400/404
            if e.response.status_code in (400, 404, 422):
                logger.error("LLM model not loaded or bad request: %s", e.response.text[:200])
                raise LLMUnavailableError(f"model not loaded: {e.response.status_code}") from e
            logger.error("LLM server error: %s", e)
            raise LLMError(str(e)) from e
        except Exception as e:
            logger.error("LLM chat failed: %s", e)
            raise LLMError(str(e)) from e

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/models")
            return resp.status_code == 200
        except Exception:
            return False

    def is_model_loaded(self) -> bool:
        """检测配置的模型是否已实际加载（LM Studio 本地模式专用：lms ps 查询）。

        /v1/models 在模型卸载后仍列出全部模型（无 loaded 状态），不可靠；
        且 LM Studio 收到未加载模型的 chat 请求会自动重载（瞬间吃回显存）。
        游戏模式下必须先查 lms ps，未加载则不应发请求。

        远程 API 模式（配置了 api_key）或无本地 lms：视为始终可用，放行交给 chat 异常处理。
        """
        if self.api_key:
            return True  # 远程 API：无"加载"概念
        lms = self.config.get("lms_path", str(Path.home() / ".lmstudio" / "bin" / "lms.exe"))
        if not Path(lms).exists():
            return True  # 非 LM Studio 本地模式（如 Ollama 或其他兼容服务）：放行
        try:
            import subprocess
            r = subprocess.run([lms, "ps"], capture_output=True, text=True, timeout=15)
            return self.model in (r.stdout or "")
        except Exception as e:
            logger.warning("lms ps check failed: %s", e)
            return True  # 查询失败时放行，交给 chat 异常处理

    def ensure_model(self) -> bool:
        """检查配置的模型是否已加载；未加载时列出可用模型。"""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/models")
            if resp.status_code != 200:
                return False
            names = [m.get("id", "") for m in resp.json().get("data", [])]
            if self.model in names:
                return True
            logger.warning("model %s not loaded (available: %s). 用 LM Studio 加载后重试。", self.model, names)
            return False
        except Exception as e:
            logger.error("cannot reach LLM server at %s: %s", self.base_url, e)
            return False

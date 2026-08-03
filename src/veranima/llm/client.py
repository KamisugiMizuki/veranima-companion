"""LLM 客户端：OpenAI 兼容接口（httpx 直调）。

兼容 LM Studio（http://localhost:1234/v1）与 Ollama（http://localhost:11434/v1）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "http://localhost:1234/v1")
        self.model = config.get("model", "qwen3:8b")
        self.temperature = config.get("temperature", 0.8)
        self.max_tokens = config.get("max_tokens", 1024)
        self.low_energy_max_tokens = config.get("low_energy_max_tokens", 256)
        self._timeout = config.get("timeout", 120.0)

    def chat(self, messages: list[dict], *, max_tokens: int | None = None, temperature: float | None = None) -> str:
        """单次对话生成。messages: [{'role','content'}, ...]"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error("LLM chat failed: %s", e)
            raise

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/models")
            return resp.status_code == 200
        except Exception:
            return False

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

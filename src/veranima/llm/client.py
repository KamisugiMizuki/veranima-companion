"""Ollama 客户端封装（ollama-python）。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, config: dict):
        import ollama
        self._client = ollama.Client(host=config.get("host", "http://localhost:11434"))
        self.model = config.get("model", "qwen3:8b")
        self.temperature = config.get("temperature", 0.8)
        self.max_tokens = config.get("max_tokens", 1024)
        self.low_energy_max_tokens = config.get("low_energy_max_tokens", 256)

    def chat(self, messages: list[dict], *, max_tokens: int | None = None, temperature: float | None = None) -> str:
        """单次对话生成。messages: [{'role','content'}, ...]"""
        try:
            resp = self._client.chat(
                model=self.model,
                messages=messages,
                options={
                    "temperature": temperature if temperature is not None else self.temperature,
                    "num_predict": max_tokens or self.max_tokens,
                },
            )
            return resp["message"]["content"].strip()
        except Exception as e:
            logger.error("LLM chat failed: %s", e)
            raise

    def embed(self, text: str) -> list[float]:
        resp = self._client.embed(model="bge-m3", input=[text])
        return list(resp.embeddings[0])

    def is_available(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception:
            return False

    def ensure_model(self) -> bool:
        """检查模型是否就绪；未就绪时给出提示。"""
        try:
            models = self._client.list()
            names = [m.model for m in models.get("models", [])]
            if self.model in names:
                return True
            logger.warning(
                "model %s not found in ollama (have: %s). "
                "导入本地 GGUF：ollama create %s -f Modelfile",
                self.model, names, self.model,
            )
            return False
        except Exception as e:
            logger.error("cannot reach ollama at %s: %s", self._client.host, e)
            return False

"""TTS 客户端：OpenAI 兼容 /v1/audio/speech（远程与本地统一接口）。

配置 base_url/model/voice 即可对接：
- 远程：任意 OpenAI 兼容 TTS API（OpenAI/硅基流动/通义等）
- 本地：Qwen3-TTS 兼容服务（base_url 指向 127.0.0.1:端口）

与 llm/client.py 同一模式：base_url 决定远程还是本地，接口不变。
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class TTSUnavailableError(RuntimeError):
    """TTS 服务不可用：连接失败 / 鉴权失败。"""


class TTSClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = config.get("base_url", "").rstrip("/")
        self.model = config.get("model", "tts-1")
        self.voice = config.get("voice", "alloy")
        self.api_key = config.get("api_key", "")
        self.response_format = config.get("response_format", "wav")  # wav/mp3/opus
        self._timeout = config.get("timeout", 60.0)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def is_available(self) -> bool:
        """base_url 配置了即可用（连接性由 synthesize 错误暴露）。"""
        return bool(self.base_url)

    def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """文本 → 音频 bytes（OpenAI /v1/audio/speech 格式）。"""
        if not self.base_url:
            raise TTSUnavailableError("TTS 未配置 base_url")
        payload = {
            "model": self.model,
            "input": text,
            "voice": voice or self.voice,
            "response_format": self.response_format,
        }
        # base_url 可能带 /v1（OpenAI 惯例）——避免 /v1/v1 重复
        url = self.base_url if self.base_url.endswith("/audio/speech") else (
            f"{self.base_url}/audio/speech" if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/audio/speech"
        )
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.content
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.error("TTS unavailable: %s", e)
            raise TTSUnavailableError(str(e)) from e
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = (e.response.text[:200] if e.response is not None else "")
            logger.error("TTS failed: %s %s", status, body)
            raise TTSUnavailableError(f"TTS {status}") from e

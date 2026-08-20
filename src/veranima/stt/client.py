"""STT 客户端：OpenAI 兼容 /v1/audio/transcriptions（远程与本地统一接口）。

配置 base_url/model 即可对接：
- 远程：任意 OpenAI 兼容 STT API（OpenAI/通义/硅基流动等）
- 本地：本地 Whisper/FunASR 兼容服务（base_url 指向 127.0.0.1:端口）

与 llm/client.py、tts/client.py 同一模式：base_url 决定远程还是本地，接口不变。
Electron 与本客户端都消费同一 `stt.base_url/model/language/timeout/api_key` 契约；本地 9890 时由 Electron 负责拉起服务，远程 URL 时直接请求远端。
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)
MIME_BY_SUFFIX = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".flac": "audio/flac", ".webm": "audio/webm",
}


class STTUnavailableError(RuntimeError):
    """STT 服务不可用：未配置 base_url / 连接失败 / 鉴权失败。"""


class STTClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = config.get("base_url", "").rstrip("/")
        self.model = config.get("model", "sensevoice-small")
        self.language = config.get("language", "auto") or "auto"
        self.language_priority = config.get("language_priority", ["zh", "en", "ja"])
        self.api_key = config.get("api_key", "")
        self._timeout = config.get("timeout", 60.0)

    def _headers(self) -> dict:
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def is_available(self) -> bool:
        """base_url 配置了即可用（连接性由 transcribe 错误暴露）。"""
        return bool(self.base_url)

    def transcribe(self, audio: bytes, *, filename: str = "audio.wav", language: str | None = None) -> str:
        """音频 bytes → 文本（OpenAI /v1/audio/transcriptions 格式）。

        multipart：file + model [+ language]。返回识别文本；失败抛 STTUnavailableError。
        未配置 base_url：logger 提示「STT 未配置」并返回 ""（调用方降级），不报错。
        """
        if not self.base_url:
            logger.info("STT 未配置（config.yaml stt.base_url 留空）——跳过识别，调用方应降级处理")
            return ""
        # base_url 可能带 /v1（OpenAI 惯例）——避免 /v1/v1 重复（与 TTS client 同款）
        url = self.base_url if self.base_url.endswith("/audio/transcriptions") else (
            f"{self.base_url}/audio/transcriptions" if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/audio/transcriptions"
        )
        data: dict = {"model": self.model, "language": language or self.language}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    url,
                    data=data,
                    files={"file": (filename, audio, MIME_BY_SUFFIX.get(Path(filename).suffix.lower(), "audio/wav"))},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return str(resp.json().get("text", "")).strip()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.error("STT unavailable: %s", e)
            raise STTUnavailableError(str(e)) from e
        except httpx.HTTPStatusError as e:
            logger.error("STT HTTP %s: %s", e.response.status_code, e.response.text[:200])
            raise STTUnavailableError(f"HTTP {e.response.status_code}") from e

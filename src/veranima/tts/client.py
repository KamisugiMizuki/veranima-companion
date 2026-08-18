"""TTS 客户端：OpenAI 兼容 /v1/audio/speech 与 GPT-SoVITS /tts 统一接口。

配置 provider 切换：
- openai（默认）：任意 OpenAI 兼容 TTS API（远程或本地 Qwen3-TTS）
- gpt-sovits：本地 GPT-SoVITS api_v2.py（POST /tts，参考音频+转录文本）

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
        self.provider = (config.get("provider") or "openai").lower()
        self.base_url = config.get("base_url", "").rstrip("/")
        self.model = config.get("model", "tts-1")
        self.voice = config.get("voice", "alloy")
        self.api_key = config.get("api_key", "")
        self.response_format = config.get("response_format", "wav")  # wav/mp3/opus
        self._timeout = config.get("timeout", 60.0)
        # GPT-SoVITS 专用（provider=gpt-sovits）
        self.ref_audio_path = config.get("ref_audio_path", "")
        self.prompt_text = config.get("prompt_text", "")
        self.prompt_lang = config.get("prompt_lang", "ja")
        self.text_lang = config.get("text_lang", "ja")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def is_available(self) -> bool:
        """base_url 配置了即可用（连接性由 synthesize 错误暴露）。"""
        return bool(self.base_url)

    def synthesize(self, text: str, *, voice: str | None = None) -> bytes | None:
        """文本 → 音频 bytes。

        provider=openai：OpenAI /v1/audio/speech 格式。
        provider=gpt-sovits：POST {base_url}/tts（参考音频+转录文本）。
        未配置 base_url：logger 提示「TTS 未配置」并返回 None（调用方降级为纯气泡），不报错。
        """
        if not self.base_url:
            logger.info("TTS 未配置（config.yaml tts.base_url 留空）——跳过合成，调用方应降级处理")
            return None
        if self.provider == "gpt-sovits":
            return self._synthesize_gptsovits(text)
        return self._synthesize_openai(text, voice)

    def _synthesize_openai(self, text: str, voice: str | None) -> bytes | None:
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

    def _synthesize_gptsovits(self, text: str) -> bytes | None:
        """GPT-SoVITS api_v2.py：POST /tts，参考音频 + 转录文本（v3+ 必需 prompt_text）。"""
        payload = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio_path,
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_lang,
            "text_split_method": "cut1",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "top_k": 15,
            "top_p": 1,
            "temperature": 1,
            "repetition_penalty": 1.2,
        }
        url = f"{self.base_url}/tts"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                return resp.content
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.error("TTS unavailable: %s", e)
            raise TTSUnavailableError(str(e)) from e
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = (e.response.text[:200] if e.response is not None else "")
            logger.error("GPT-SoVITS failed: %s %s", status, body)
            raise TTSUnavailableError(f"GPT-SoVITS {status}") from e

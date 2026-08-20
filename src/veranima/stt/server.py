"""本地 SenseVoice OpenAI 兼容 STT 服务。

由 GPT-SoVITS Python 3.9 runtime 启动：
python -m veranima.stt.server --model-path data/models/sensevoice-small --port 9890
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
import uvicorn

from .sensevoice import SenseVoiceBackend, STTBackendError

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 20 * 1024 * 1024
ALLOWED_TYPES = {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/ogg", "audio/flac", "audio/webm"}
TYPE_SUFFIX = {
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3", "audio/ogg": ".ogg", "audio/flac": ".flac",
    "audio/webm": ".webm",
}


def _audio_suffix(data: bytes) -> str:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ".mp3"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    return ""


def create_app(backend: SenseVoiceBackend, *, max_audio_bytes: int = MAX_AUDIO_BYTES) -> FastAPI:
    app = FastAPI(title="Veranima STT")

    @app.get("/health")
    async def health():
        return {"ok": True, "provider": "sensevoice", "loaded": backend.loaded}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        request: Request,
        file: UploadFile = File(...),
        model: str = Form("sensevoice-small"),
        language: str = Form("auto"),
    ):
        if request.headers.get("origin"):
            raise HTTPException(403, "不接受浏览器跨域请求")
        content_type = (file.content_type or "").split(";", 1)[0].lower()
        if content_type and content_type not in ALLOWED_TYPES:
            raise HTTPException(415, "不支持的音频类型")
        data = await file.read(max_audio_bytes + 1)
        if not data:
            raise HTTPException(400, "音频为空")
        if len(data) > max_audio_bytes:
            raise HTTPException(413, "音频过大")
        suffix = _audio_suffix(data)
        if not suffix or (content_type and TYPE_SUFFIX[content_type] != suffix):
            raise HTTPException(415, "音频容器与类型不匹配")
        fd, name = tempfile.mkstemp(prefix="veranima-stt-", suffix=suffix)
        os.close(fd)
        path = Path(name)
        try:
            path.write_bytes(data)
            try:
                text = await asyncio.to_thread(backend.transcribe, path, language=language or "auto")
            except STTBackendError as exc:
                logger.warning("stt backend unavailable: %s", exc)
                raise HTTPException(503, str(exc)) from exc
            return JSONResponse({
                "text": text,
                "language": language or "auto",
                "provider": "sensevoice",
                "model": model,
            })
        finally:
            path.unlink(missing_ok=True)

    return app


def main() -> None:
    try:
        from veranima.config import load_config
        config = load_config().get("stt", {}) or {}
    except Exception as exc:
        logger.warning("stt config unavailable, using defaults: %s", exc)
        config = {}
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", default=config.get("model_path") or "data/models/sensevoice-small")
    parser.add_argument(
        "--vad-model-path",
        default=config.get("vad_model_path") or "tts/gpt-sovits/tools/asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    )
    parser.add_argument("--device", default=config.get("device") or "cpu")
    parser.add_argument("--language", default=config.get("language") or "auto")
    parser.add_argument("--port", type=int, default=9890)
    parser.add_argument("--max-audio-bytes", type=int, default=int(config.get("max_audio_bytes") or MAX_AUDIO_BYTES))
    args = parser.parse_args()
    backend = SenseVoiceBackend(
        args.model_path,
        device=args.device,
        language=args.language,
        language_priority=config.get("language_priority") or ["zh", "en", "ja"],
        vad_model_path=args.vad_model_path or None,
    )
    uvicorn.run(create_app(backend, max_audio_bytes=max(1024, args.max_audio_bytes)), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

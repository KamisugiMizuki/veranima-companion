"""Qwen3-TTS OpenAI 兼容服务（远程/本地统一接口的本地实现）。

起一个 FastAPI 服务，暴露 OpenAI 格式的 POST /v1/audio/speech：
  {"model": "...", "input": "文本", "voice": "...", "response_format": "wav"}
  → 音频 bytes

模型：data/models/qwen3-tts/Qwen3-TTS-12Hz-1.7B-Base（transformers 加载）。
启动：python -m veranima.tts.server --port 9880
"""
from __future__ import annotations

import argparse
import io
import logging

from starlette.requests import Request

logger = logging.getLogger("veranima.tts.server")

# 模型路径（相对项目根；环境变量可覆盖）
_MODEL_DIR = "data/models/qwen3-tts/Qwen3-TTS-12Hz-1.7B-Base"
_SPEECH_TOKENIZER_DIR = "data/models/qwen3-tts/Qwen3-TTS-Tokenizer-12Hz"

_model = None
_tokenizer = None
_device = None


def _load_model():
    """延迟加载模型（首次请求时；避免 import 即占用显存）。"""
    global _model, _tokenizer, _device
    if _model is not None:
        return _model, _tokenizer, _device
    import torch
    from transformers import AutoTokenizer, Qwen3TTSForConditionalGeneration
    from veranima.config import ROOT

    model_path = ROOT / _MODEL_DIR
    tok_path = ROOT / _SPEECH_TOKENIZER_DIR
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("loading Qwen3-TTS from %s (device=%s) ...", model_path, _device)
    _tokenizer = AutoTokenizer.from_pretrained(tok_path)
    _model = Qwen3TTSForConditionalGeneration.from_pretrained(
        model_path, torch_dtype=torch.float16 if _device == "cuda" else torch.float32
    ).to(_device)
    logger.info("model loaded")
    return _model, _tokenizer, _device


def synthesize(text: str) -> bytes:
    """文本 → WAV bytes。"""
    import torch

    model, tokenizer, device = _load_model()
    inputs = tokenizer(text, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.generate(**inputs)
    audio = output[0].cpu().numpy()
    # 转 WAV（16kHz mono 16bit）
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        import numpy as np
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def create_app():
    """FastAPI 应用（/v1/audio/speech）。"""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Veranima TTS")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def audio_speech(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        text = str(body.get("input") or "").strip()
        if not text:
            raise HTTPException(400, "input required")
        # response_format 只支持 wav（Qwen3-TTS 原生输出波形）
        fmt = body.get("response_format", "wav")
        if fmt not in ("wav",):
            raise HTTPException(400, f"unsupported response_format: {fmt}")
        try:
            audio = await _run_sync(synthesize, text)
        except Exception as e:
            logger.error("synthesize failed: %s", e)
            raise HTTPException(500, str(e))
        from fastapi.responses import Response
        return Response(content=audio, media_type="audio/wav")

    return app


async def _run_sync(fn, *args):
    """线程池跑 CPU/GPU 推理（不阻塞事件循环）。"""
    import asyncio
    return await asyncio.to_thread(fn, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Qwen3-TTS OpenAI 兼容服务")
    ap.add_argument("--port", type=int, default=9880)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    import uvicorn
    app = create_app()
    logger.info("TTS server on http://%s:%s/v1/audio/speech", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

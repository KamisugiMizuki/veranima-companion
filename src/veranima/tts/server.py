"""Qwen3-TTS OpenAI 兼容服务（远程/本地统一接口的本地实现）。

起一个 FastAPI 服务，暴露 OpenAI 格式的 POST /v1/audio/speech：
  {"model": "...", "input": "文本", "voice": "...", "response_format": "wav"}
  → 音频 bytes

模型：data/models/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice
（qwen-tts 包；CustomVoice 内置 9 种音色，无需参考音频。
  音色映射：alloy→Vivian（中文明亮女声）、echo→Serena（温柔女声）等，未识别回退 Vivian）

启动：python -m veranima.tts.server --port 9880
"""
from __future__ import annotations

import argparse
import io
import logging

from starlette.requests import Request

logger = logging.getLogger("veranima.tts.server")

# 模型路径（相对项目根）
_MODEL_DIR = "data/models/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice"
_SPEECH_TOKENIZER_DIR = "data/models/qwen3-tts/Qwen3-TTS-Tokenizer-12Hz"

# OpenAI voice → Qwen3-TTS speaker 映射（CustomVoice 内置音色）
VOICE_MAP = {
    "alloy": "Vivian",     # 明亮、略带锐利的年轻女声（中文）
    "echo": "Serena",      # 温暖柔和的年轻女声（中文）
    "fable": "Vivian",
    "onyx": "Uncle_Fu",    # 低沉圆润的男声
    "nova": "Serena",
    "shimmer": "Vivian",
}

_model = None
_tokenizer = None
_device = None


def _load_model():
    """延迟加载模型（首次请求时；避免 import 即占用显存）。"""
    global _model, _tokenizer, _device
    if _model is not None:
        return _model, _tokenizer, _device
    import torch
    from qwen_tts import Qwen3TTSModel, Qwen3TTSTokenizer
    from veranima.config import ROOT

    model_path = ROOT / _MODEL_DIR
    tok_path = ROOT / _SPEECH_TOKENIZER_DIR
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("loading Qwen3-TTS CustomVoice from %s (device=%s) ...", model_path, _device)
    _tokenizer = Qwen3TTSTokenizer.from_pretrained(tok_path)
    _model = Qwen3TTSModel.from_pretrained(
        model_path,
        device_map=_device,
        dtype=torch.bfloat16 if _device == "cuda" else torch.float32,
        # sdpa 实测比 eager 快 ~15%（attention 非瓶颈；flash-attn 编译收益有限不值 2-3h）
        attn_implementation="sdpa",
    )
    logger.info("model loaded")
    return _model, _tokenizer, _device


def synthesize(text: str, voice: str = "alloy") -> bytes:
    """文本 → WAV bytes。voice 用 CustomVoice 内置音色。"""
    import io as _io
    import numpy as np

    model, _tok, _ = _load_model()
    speaker = VOICE_MAP.get(voice, "Vivian")
    # CustomVoice 生成（中文；auto 语言自适应）
    wavs, sr = model.generate_custom_voice(
        text=text,
        language="Chinese",
        speaker=speaker,
    )
    audio = np.asarray(wavs[0], dtype=np.float32)
    # 转 WAV（16bit PCM）
    import wave
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def create_app():
    """FastAPI 应用（/v1/audio/speech）。"""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import Response

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
        fmt = body.get("response_format", "wav")
        if fmt not in ("wav",):
            raise HTTPException(400, f"unsupported response_format: {fmt}")
        voice = str(body.get("voice") or "alloy")
        try:
            audio = await _run_sync(synthesize, text, voice)
        except Exception as e:
            logger.error("synthesize failed: %s", e)
            raise HTTPException(500, str(e))
        return Response(content=audio, media_type="audio/wav")

    return app


async def _run_sync(fn, *args):
    """线程池跑 GPU 推理（不阻塞事件循环）。"""
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

"""Qwen3-TTS OpenAI 兼容服务（远程/本地统一接口的本地实现）。

起一个 FastAPI 服务，暴露 OpenAI 格式的 POST /v1/audio/speech：
  {"model": "...", "input": "文本", "voice": "<参考音频路径>", "response_format": "wav"}
  → 音频 bytes

模型：data/models/qwen3-tts/Qwen3-TTS-12Hz-1.7B-Base（声音克隆模型）
voice 字段 = 参考音频路径（克隆音色；x_vector_only_mode 纯音色特征，无需参考文本）。
默认参考音频：characters/yuki/example_voices/yuki.mp3（可用环境变量
VERANIMA_TTS_REF_AUDIO 覆盖）。

启动：python -m veranima.tts.server --port 9880
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import threading

from starlette.requests import Request

logger = logging.getLogger("veranima.tts.server")

# 模型路径（相对项目根）：0.6B-Base = 声音克隆模型（需参考音频）
# 2026-08-19 从 1.7B 切 0.6B：1.7B 克隆 AR 解码慢，对话句间间隔过长
_MODEL_DIR = "data/models/qwen3-tts/Qwen3-TTS-12Hz-0.6B-Base"
_SPEECH_TOKENIZER_DIR = "data/models/qwen3-tts/Qwen3-TTS-Tokenizer-12Hz"
# 默认参考音频（克隆音色来源；voice 参数可覆盖）
_DEFAULT_REF_AUDIO = os.environ.get(
    "VERANIMA_TTS_REF_AUDIO",
    "characters/yuki/example_voices/yuki.mp3",
)

_model = None
_tokenizer = None
_device = None
_clone_prompt = None      # 克隆 prompt 缓存（参考音频固定 → 只构建一次）
_clone_prompt_ref = None  # 已缓存的参考音频路径


def _get_clone_prompt(ref_audio: str):
    """克隆 prompt 缓存：参考音频不变则复用（x-vector 提取只做一次）。"""
    global _clone_prompt, _clone_prompt_ref
    if _clone_prompt is not None and _clone_prompt_ref == ref_audio:
        return _clone_prompt
    model, _tok, _ = _load_model()
    _clone_prompt = model.create_voice_clone_prompt(ref_audio, x_vector_only_mode=True)
    _clone_prompt_ref = ref_audio
    return _clone_prompt


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
    logger.info("loading Qwen3-TTS Base(clone) from %s (device=%s) ...", model_path, _device)
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


# 合成全局锁：qwen_tts 模型推理线程不安全（多请求并发 → 互相阻塞/超时实测）
# ponytail: 全局串行锁，若多并发合成成为瓶颈再换队列+双实例
_synth_lock = threading.Lock()


def synthesize(text: str, voice: str = "") -> bytes:
    """文本 → WAV bytes。voice = 参考音频路径（克隆音色）；空 → 默认 yuki.mp3。"""
    import io as _io
    import numpy as np
    from veranima.config import ROOT

    with _synth_lock:  # 串行化模型推理
        return _synthesize_locked(text, voice)


def _synthesize_locked(text: str, voice: str) -> bytes:
    import io as _io
    import numpy as np
    from veranima.config import ROOT

    model, _tok, _ = _load_model()
    # voice 参数 = 参考音频路径；空/默认值 → 用默认样本
    ref_audio = voice if voice and os.path.exists(voice) else str(ROOT / _DEFAULT_REF_AUDIO)
    logger.info("clone voice ref_audio=%s (voice param=%r)", ref_audio, voice)
    # 克隆生成（x_vector_only：纯音色特征，无需参考文本；prompt 缓存复用）
    wavs, sr = model.generate_voice_clone(
        text=text,
        language="Auto",
        voice_clone_prompt=_get_clone_prompt(ref_audio),
        non_streaming_mode=True,  # 整句一次性生成（False 模拟流式输入，实测更慢）
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

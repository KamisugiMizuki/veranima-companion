"""STT backend：复用 GPT-SoVITS runtime 中的 FunASR。

本模块故意不在 Veranima venv 导入 funasr；服务进程由
``tts/gpt-sovits/runtime/python.exe`` 启动，运行时通过 PYTHONPATH 注入本包。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class STTBackendError(RuntimeError):
    pass


class SenseVoiceBackend:
    """SenseVoiceSmall 单例模型包装；默认 CPU，支持中英日混合 auto。"""

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "cpu",
        language: str = "auto",
        language_priority: list[str] | tuple[str, ...] = ("zh", "en", "ja"),
        vad_model_path: str | Path | None = None,
    ):
        self.model_path = str(model_path)
        self.device = device or "cpu"
        self.language = language or "auto"
        self.language_priority = tuple(
            item for item in (str(value).strip() for value in language_priority)
            if item and item != "auto"
        )
        self.vad_model_path = str(vad_model_path) if vad_model_path else ""
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        if not Path(self.model_path).is_dir():
            raise STTBackendError(f"SenseVoice 模型目录不存在: {self.model_path}")
        if self.vad_model_path and not Path(self.vad_model_path).is_dir():
            raise STTBackendError(f"FSMN-VAD 模型目录不存在: {self.vad_model_path}")
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise STTBackendError("当前 Python 没有 FunASR；请使用 GPT-SoVITS runtime") from exc
        try:
            model_kwargs = dict(
                model=self.model_path,
                device=self.device,
                disable_update=True,
            )
            if self.vad_model_path:
                model_kwargs.update(
                    vad_model=self.vad_model_path,
                    vad_kwargs={"max_single_segment_time": 10_000},
                )
            self._model = AutoModel(**model_kwargs)
        except Exception as exc:
            raise STTBackendError(f"SenseVoice 加载失败: {exc}") from exc
        logger.info("SenseVoice loaded: path=%s device=%s", self.model_path, self.device)

    def transcribe(self, audio_path: str | Path, *, language: str | None = None) -> str:
        with self._lock:
            self.load()
            selected = language or self.language or "auto"
            languages = (selected,) + (self.language_priority if selected == "auto" else ())
            for current_language in languages:
                try:
                    generate_kwargs = dict(
                        input=str(audio_path),
                        language=current_language,
                        use_itn=True,
                        batch_size_s=60 if self.vad_model_path else 300,
                    )
                    if self.vad_model_path:
                        generate_kwargs.update(merge_vad=False, merge_length_s=15)
                    result = self._model.generate(**generate_kwargs)
                except Exception as exc:
                    raise STTBackendError(f"SenseVoice 推理失败: {exc}") from exc
                if not result:
                    continue
                text = str(result[0].get("text", "")).strip()
                try:
                    from funasr.utils.postprocess_utils import rich_transcription_postprocess
                    text = rich_transcription_postprocess(text)
                except Exception:
                    pass
                if text.strip():
                    return text.strip()
            return ""

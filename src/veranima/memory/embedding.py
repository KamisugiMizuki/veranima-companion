"""Embedding provider：fastembed（本地 ONNX）或 Ollama embedding。

优先 fastembed；若模型缺失/加载失败，回退 ollama（模型名以 "ollama:" 前缀配置）。
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedProvider:
    """fastembed 本地 ONNX 推理（系统 3.14 已装 0.8.0）。"""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        self._model = None
        # bge-m3 为 1024 维
        self.dim = 1024

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding
            logger.info("loading fastembed model %s ...", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
            logger.info("fastembed model loaded")
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure()
        return [v.tolist() for v in model.embed(texts)]


class OllamaEmbedProvider:
    """Ollama embedding（ollama-python，模型如 bge-m3 / nomic-embed-text）。"""

    def __init__(self, host: str, model: str = "bge-m3"):
        import ollama
        self._client = ollama.Client(host=host)
        self.model = model
        self.dim = 1024  # bge-m3；其他模型需按实际调整

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embed(model=self.model, input=texts)
        return [list(v) for v in resp.embeddings]


class SentenceTransformersProvider:
    """本地 sentence-transformers 模型（ModelScope 下载的 safetensors 目录，torch 推理）。

    配置形如 'local:data/models/bge-m3'。
    """

    def __init__(self, model_path: str):
        from sentence_transformers import SentenceTransformer
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        logger.info("loading sentence-transformers model from %s (device=%s)", model_path, device)
        self._model = SentenceTransformer(model_path, device=device)
        self.dim = self._model.get_sentence_embedding_dimension()
        logger.info("embedding model loaded, dim=%s", self.dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


def make_provider(config: dict) -> EmbeddingProvider:
    """按配置构造 provider。

    - 'local:<path>'：本地 sentence-transformers（ModelScope 下载）
    - 'BAAI/bge-m3' 等：fastembed（HF 下载，慎用）
    - 'ollama:<model>'：Ollama embedding
    """
    spec = config.get("embedding_model", "")
    if spec.startswith("local:"):
        return SentenceTransformersProvider(spec.split(":", 1)[1])
    if spec.startswith("ollama:"):
        return OllamaEmbedProvider(config.get("host", "http://localhost:11434"), spec.split(":", 1)[1])
    try:
        return FastEmbedProvider(spec)
    except Exception as e:
        logger.warning("fastembed unavailable (%s), falling back to ollama embedding", e)
        return OllamaEmbedProvider(config.get("host", "http://localhost:11434"), "bge-m3")

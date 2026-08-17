"""Embedding provider：本地 sentence-transformers / OpenAI 兼容 API / Ollama / fastembed。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import httpx

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
        # sentence-transformers 5.x 新 API；兼容旧名
        getter = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        self.dim = getter()
        logger.info("embedding model loaded, dim=%s", self.dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


class OpenAIEmbedProvider:
    """OpenAI 兼容 embeddings API（/v1/embeddings）。

    远程 API（DeepSeek/通义/硅基流动等）均可；
    base_url/api_key 复用 llm 段配置。模型名如 'bge-m3' / 'text-embedding-v4'（通义）。
    """

    def __init__(self, base_url: str, api_key: str = "", model: str = "bge-m3", dim: int = 1024):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model, "input": texts}
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        # 按 index 排序，保证与输入顺序一致
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return [list(d["embedding"]) for d in items]


def make_provider(config: dict, llm_config: dict | None = None) -> EmbeddingProvider:
    """按配置构造 provider。

    - 'local:<path>'：本地 sentence-transformers（ModelScope 下载）
    - 'openai:<model>'：OpenAI 兼容 /v1/embeddings（复用 llm 段 base_url/api_key）
    - 'ollama:<model>'：Ollama embedding
    - 'BAAI/bge-m3' 等：fastembed（HF 下载，慎用）
    - 空/缺失：自动尝试 root/data/models/bge-m3 → ollama → 清晰报错
    """
    llm_config = llm_config or {}
    spec = (config.get("embedding_model") or "").strip()

    if not spec:
        # 配置缺省：尝试项目内本地模型（root 由 cli.py 注入），再 ollama，最后清晰报错
        root = config.get("root") or config.get("project_root") or "."
        local_default = Path(root) / "data" / "models" / "bge-m3"
        if local_default.exists():
            logger.info("embedding_model 未配置，自动使用 %s", local_default)
            return SentenceTransformersProvider(str(local_default))
        if llm_config.get("api_key") or llm_config.get("base_url"):
            logger.warning("embedding_model 未配置且无本地模型，回退 OpenAI 兼容 API")
            return OpenAIEmbedProvider(llm_config.get("base_url", ""), llm_config.get("api_key", ""))
        logger.warning("embedding_model 未配置且无本地模型，回退 Ollama embedding")
        return OllamaEmbedProvider(config.get("host", "http://localhost:11434"), "bge-m3")

    if spec.startswith("local:"):
        path = spec.split(":", 1)[1]
        if Path(path).exists():
            return SentenceTransformersProvider(path)
        # 本地模型缺失（他人 clone 场景）：尝试 API 模式，无则清晰报错
        if llm_config.get("api_key") or llm_config.get("base_url"):
            logger.warning("local embedding model %s missing, falling back to openai-compatible API", path)
            return OpenAIEmbedProvider(llm_config.get("base_url", ""), llm_config.get("api_key", ""))
        raise RuntimeError(
            f"embedding 本地模型不存在: {path}。请下载（ModelScope BAAI/bge-m3）或配置 openai:<model> 使用 API。"
        )
    if spec.startswith("openai:"):
        return OpenAIEmbedProvider(llm_config.get("base_url", ""), llm_config.get("api_key", ""), spec.split(":", 1)[1])
    if spec.startswith("ollama:"):
        return OllamaEmbedProvider(config.get("host", "http://localhost:11434"), spec.split(":", 1)[1])
    try:
        return FastEmbedProvider(spec)
    except Exception as e:
        logger.warning("fastembed unavailable (%s), falling back to ollama embedding", e)
        return OllamaEmbedProvider(config.get("host", "http://localhost:11434"), "bge-m3")

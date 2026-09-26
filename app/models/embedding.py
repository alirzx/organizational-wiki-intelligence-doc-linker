from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np
import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingBatch:
    dense: list[list[float]]
    sparse_indices: list[list[int]]
    sparse_values: list[list[float]]


class EmbeddingService(Protocol):
    def encode_documents(self, texts: list[str]) -> EmbeddingBatch: ...
    def encode_query(self, text: str) -> tuple[list[float], list[int], list[float]]: ...
    def count_tokens(self, text: str) -> int: ...


class BGEEmbeddingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from FlagEmbedding import BGEM3FlagModel

                kwargs = {
                    "use_fp16": self.settings.embedding_use_fp16,
                    "devices": self.settings.embedding_device,
                }
                cache_dir = os.getenv("HF_HOME") or os.getenv("HF_HUB_CACHE")
                if cache_dir:
                    kwargs["cache_dir"] = cache_dir
                logger.info(
                    "Loading embedding model=%s device=%s",
                    self.settings.embedding_model,
                    self.settings.embedding_device,
                )
                self._model = BGEM3FlagModel(self.settings.embedding_model, **kwargs)
        return self._model

    def _get_tokenizer(self):
        if self._tokenizer is not None:
            return self._tokenizer
        with self._lock:
            if self._tokenizer is None:
                from transformers import AutoTokenizer

                self._tokenizer = AutoTokenizer.from_pretrained(
                    self.settings.embedding_model,
                    cache_dir=os.getenv("HF_HOME") or None,
                )
        return self._tokenizer

    @lru_cache(maxsize=8192)
    def count_tokens(self, text: str) -> int:
        tokenizer = self._get_tokenizer()
        return len(tokenizer.encode(text, add_special_tokens=False))

    @staticmethod
    def _sparse_lists(weights) -> tuple[list[list[int]], list[list[float]]]:
        all_indices: list[list[int]] = []
        all_values: list[list[float]] = []
        for item in weights or []:
            pairs = []
            if isinstance(item, dict):
                for key, value in item.items():
                    try:
                        pairs.append((int(key), float(value)))
                    except (TypeError, ValueError):
                        continue
            pairs.sort(key=lambda pair: pair[0])
            all_indices.append([item[0] for item in pairs])
            all_values.append([item[1] for item in pairs])
        return all_indices, all_values

    def _encode(self, texts: list[str], *, query: bool) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch([], [], [])
        model = self._get_model()
        method = model.encode_queries if query and hasattr(model, "encode_queries") else (
            model.encode_corpus if not query and hasattr(model, "encode_corpus") else model.encode
        )
        output = method(
            texts,
            batch_size=self.settings.embedding_batch_size,
            max_length=self.settings.embedding_max_length,
            return_dense=True,
            return_sparse=self.settings.enable_sparse,
            return_colbert_vecs=False,
        )
        dense_array = np.asarray(output["dense_vecs"], dtype=np.float32)
        dense = [row.tolist() for row in dense_array]
        if self.settings.enable_sparse:
            indices, values = self._sparse_lists(output.get("lexical_weights"))
        else:
            indices, values = ([[] for _ in texts], [[] for _ in texts])
        while len(indices) < len(texts):
            indices.append([])
            values.append([])
        return EmbeddingBatch(dense, indices, values)

    def encode_documents(self, texts: list[str]) -> EmbeddingBatch:
        return self._encode(texts, query=False)

    def encode_query(self, text: str) -> tuple[list[float], list[int], list[float]]:
        batch = self._encode([text], query=True)
        return batch.dense[0], batch.sparse_indices[0], batch.sparse_values[0]


class HttpEmbeddingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._tokenizer = None
        self._tokenizer_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        return ({"Authorization": f"Bearer {self.settings.model_service_token}"}
                if self.settings.model_service_token else {})

    def _get_tokenizer(self):
        if self._tokenizer is not None:
            return self._tokenizer
        with self._tokenizer_lock:
            if self._tokenizer is None:
                from transformers import AutoTokenizer

                self._tokenizer = AutoTokenizer.from_pretrained(
                    self.settings.embedding_model,
                    cache_dir=os.getenv("HF_HOME") or None,
                    local_files_only=True,
                )
        return self._tokenizer

    @lru_cache(maxsize=8192)
    def count_tokens(self, text: str) -> int:
        # API/worker keep only the lightweight tokenizer in memory. The model weights
        # stay in model-service, while chunk limits remain exact for Persian/mixed text.
        tokenizer = self._get_tokenizer()
        return len(tokenizer.encode(text, add_special_tokens=False))

    def _encode(self, texts: list[str], mode: str) -> EmbeddingBatch:
        response = httpx.post(
            f"{self.settings.model_service_url.rstrip('/')}/internal/embed",
            json={"texts": texts, "mode": mode},
            headers=self._headers(), timeout=self.settings.model_service_timeout_seconds, trust_env=False,
        )
        response.raise_for_status()
        data = response.json()
        return EmbeddingBatch(
            dense=data["dense"], sparse_indices=data["sparse_indices"], sparse_values=data["sparse_values"]
        )

    def encode_documents(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch([], [], [])
        dense: list[list[float]] = []
        sparse_indices: list[list[int]] = []
        sparse_values: list[list[float]] = []
        # Keep each private HTTP request bounded even for very large documents.
        for start in range(0, len(texts), 256):
            batch = self._encode(texts[start : start + 256], "documents")
            dense.extend(batch.dense)
            sparse_indices.extend(batch.sparse_indices)
            sparse_values.extend(batch.sparse_values)
        return EmbeddingBatch(dense, sparse_indices, sparse_values)

    def encode_query(self, text: str) -> tuple[list[float], list[int], list[float]]:
        batch = self._encode([text], "query")
        return batch.dense[0], batch.sparse_indices[0], batch.sparse_values[0]


class MockEmbeddingService:
    """Deterministic dependency-light backend for tests and smoke runs."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def count_tokens(self, text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def _dense(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = [((digest[i % len(digest)] / 255.0) * 2.0) - 1.0 for i in range(self.settings.qdrant_vector_size)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    @staticmethod
    def _sparse(text: str) -> tuple[list[int], list[float]]:
        weights: dict[int, float] = {}
        for token in text.casefold().split():
            idx = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big") % 250000
            weights[idx] = max(weights.get(idx, 0.0), 1.0)
        pairs = sorted(weights.items())
        return [x for x, _ in pairs], [v for _, v in pairs]

    def encode_documents(self, texts: list[str]) -> EmbeddingBatch:
        sparse = [self._sparse(text) for text in texts]
        return EmbeddingBatch(
            [self._dense(text) for text in texts],
            [item[0] for item in sparse],
            [item[1] for item in sparse],
        )

    def encode_query(self, text: str) -> tuple[list[float], list[int], list[float]]:
        i, v = self._sparse(text)
        return self._dense(text), i, v


def build_embedder(settings: Settings) -> EmbeddingService:
    if settings.embedding_backend == "mock":
        return MockEmbeddingService(settings)
    if settings.embedding_backend == "http":
        return HttpEmbeddingService(settings)
    return BGEEmbeddingService(settings)

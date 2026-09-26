from __future__ import annotations

import logging
import os
import threading

import httpx
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class BGEReranker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from FlagEmbedding import FlagReranker

                kwargs = {"use_fp16": self.settings.reranker_use_fp16}
                cache_dir = os.getenv("HF_HOME") or os.getenv("HF_HUB_CACHE")
                if cache_dir:
                    kwargs["cache_dir"] = cache_dir
                # Current FlagEmbedding accepts devices; keep compatibility with older builds.
                try:
                    self._model = FlagReranker(
                        self.settings.reranker_model,
                        devices=[self.settings.reranker_device],
                        **kwargs,
                    )
                except TypeError:
                    self._model = FlagReranker(self.settings.reranker_model, **kwargs)
                logger.info(
                    "Loaded reranker model=%s device=%s",
                    self.settings.reranker_model,
                    self.settings.reranker_device,
                )
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = self._get_model()
        pairs = [[query, passage] for passage in passages]
        scores = model.compute_score(
            pairs,
            normalize=True,
            max_length=self.settings.reranker_max_length,
        )
        if isinstance(scores, (float, int)):
            return [float(scores)]
        return [float(value) for value in scores]


class HttpReranker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        headers = ({"Authorization": f"Bearer {self.settings.model_service_token}"}
                   if self.settings.model_service_token else {})
        response = httpx.post(
            f"{self.settings.model_service_url.rstrip('/')}/internal/rerank",
            json={"query": query, "passages": passages}, headers=headers,
            timeout=self.settings.model_service_timeout_seconds, trust_env=False,
        )
        response.raise_for_status()
        return [float(value) for value in response.json()["scores"]]


class MockReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        q = set(query.casefold().split())
        result = []
        for passage in passages:
            p = set(passage.casefold().split())
            result.append(len(q & p) / max(1, len(q | p)))
        return result


class NoopReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        # Empty scores tell RetrievalService to preserve Qdrant retrieval ordering/scores.
        return []


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_backend == "mock":
        return MockReranker()
    if settings.reranker_backend == "none":
        return NoopReranker()
    if settings.reranker_backend == "http":
        return HttpReranker(settings)
    return BGEReranker(settings)

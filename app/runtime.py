from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.models.embedding import build_embedder
from app.models.reranker import build_reranker
from app.services.callbacks import CallbackClient
from app.services.deleter import DeleteService
from app.services.indexer import IndexingService
from app.services.job_store import JobStore
from app.services.retrieval import RetrievalService
from app.storage.minio_store import MinIOStore
from app.vector.qdrant_store import QdrantStore


@lru_cache(maxsize=1)
def get_jobs() -> JobStore:
    return JobStore(get_settings())


@lru_cache(maxsize=1)
def get_storage() -> MinIOStore:
    return MinIOStore(get_settings())


@lru_cache(maxsize=1)
def get_vector() -> QdrantStore:
    return QdrantStore(get_settings())


@lru_cache(maxsize=1)
def get_embedder():
    return build_embedder(get_settings())


@lru_cache(maxsize=1)
def get_reranker():
    return build_reranker(get_settings())


@lru_cache(maxsize=1)
def get_callbacks() -> CallbackClient:
    return CallbackClient(get_settings())


@lru_cache(maxsize=1)
def get_indexer() -> IndexingService:
    return IndexingService(
        get_settings(), get_jobs(), get_storage(), get_vector(), get_embedder(), get_callbacks()
    )


@lru_cache(maxsize=1)
def get_deleter() -> DeleteService:
    return DeleteService(get_settings(), get_jobs(), get_storage(), get_vector(), get_callbacks())


@lru_cache(maxsize=1)
def get_retrieval() -> RetrievalService:
    return RetrievalService(get_settings(), get_embedder(), get_reranker(), get_vector())

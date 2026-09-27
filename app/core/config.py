from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DOC_LINKER_",
        case_sensitive=False,
        extra="ignore",
    )

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8090, ge=1, le=65535)
    api_auth_token: str | None = None
    log_level: str = "INFO"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "media"
    document_prefix: str = "documents"
    linking_dir: str = "Linking"
    minio_auto_create_bucket: bool = False
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    write_audit_artifacts: bool = True

    # Shared Redis service on the external Wiki Hami network. Keep Celery
    # broker/results and Stage-3 durable job state in separate logical DBs.
    redis_url: str = "redis://redis:6379/5"
    celery_broker_url: str | None = "redis://redis:6379/3"
    celery_result_backend: str | None = "redis://redis:6379/4"
    celery_queue: str = "wiki_hami_doc_linker"
    job_ttl_seconds: int = Field(default=604800, ge=300)
    lock_ttl_seconds: int = Field(default=3600, ge=30)
    task_max_retries: int = Field(default=3, ge=0)
    task_retry_backoff_seconds: int = Field(default=3, ge=1)

    qdrant_url: str = "http://doc-linker-qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "wiki_chunks_v1"
    qdrant_dense_vector_name: str = "dense"
    qdrant_sparse_vector_name: str = "sparse"
    qdrant_vector_size: int = Field(default=1024, ge=1)
    qdrant_timeout_seconds: float = Field(default=30.0, ge=1.0)
    qdrant_upsert_batch_size: int = Field(default=64, ge=1, le=1000)

    embedding_backend: Literal["bge", "mock", "http"] = "bge"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"
    embedding_use_fp16: bool = False
    embedding_batch_size: int = Field(default=8, ge=1)
    embedding_max_length: int = Field(default=1024, ge=64, le=8192)
    enable_sparse: bool = True

    reranker_backend: Literal["bge", "mock", "none", "http"] = "bge"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = "cpu"
    reranker_use_fp16: bool = False
    reranker_max_length: int = Field(default=1024, ge=64, le=8192)
    model_service_url: str = "http://model-service:8091"
    model_service_token: str | None = None
    model_service_timeout_seconds: float = Field(default=180.0, ge=1.0)
    model_service_warmup: bool = True

    chunk_target_tokens: int = Field(default=600, ge=50)
    chunk_max_tokens: int = Field(default=900, ge=100)
    chunk_min_tokens: int = Field(default=120, ge=10)
    chunk_overlap_tokens: int = Field(default=80, ge=0)
    semantic_break_percentile: float = Field(default=25.0, ge=0.0, le=100.0)

    retrieval_mode: Literal["dense", "hybrid"] = "hybrid"
    retrieval_candidate_limit: int = Field(default=40, ge=5, le=500)
    search_default_limit: int = Field(default=5, ge=1, le=100)
    search_max_limit: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1)

    callback_url: str | None = None
    callback_token: str | None = None
    callback_timeout_seconds: float = Field(default=15.0, ge=1.0)
    callback_max_attempts: int = Field(default=3, ge=1, le=10)

    pipeline_version: str = "1.0.0"
    chunker_version: str = "1.0.0"

    def model_post_init(self, __context: object) -> None:
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        if self.chunk_min_tokens > self.chunk_target_tokens:
            raise ValueError("chunk_min_tokens must be <= chunk_target_tokens")
        if self.chunk_target_tokens > self.chunk_max_tokens:
            raise ValueError("chunk_target_tokens must be <= chunk_max_tokens")
        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError("chunk_overlap_tokens must be < chunk_max_tokens")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

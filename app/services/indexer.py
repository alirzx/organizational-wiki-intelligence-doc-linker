from __future__ import annotations

import logging
import time
from typing import Any

from app.api.schemas import IndexReadyRequest
from app.artifacts.base import AdapterContext
from app.artifacts.factory import ArtifactAdapterFactory
from app.chunking.semantic import SemanticChunker
from app.core.config import Settings
from app.models.domain import EmbeddedChunk, compose_semantic_passage
from app.models.embedding import EmbeddingService
from app.services.callbacks import CallbackClient
from app.services.job_store import JobStore, utc_now
from app.storage.minio_store import MinIOStore
from app.vector.qdrant_store import QdrantStore

logger = logging.getLogger(__name__)


class IndexingService:
    def __init__(
        self, settings: Settings, jobs: JobStore, storage: MinIOStore, vector: QdrantStore,
        embedder: EmbeddingService, callbacks: CallbackClient,
    ):
        self.settings = settings
        self.jobs = jobs
        self.storage = storage
        self.vector = vector
        self.embedder = embedder
        self.callbacks = callbacks

    def _audit_root(self, document_id: str) -> str:
        return f"{self.settings.document_prefix}/{document_id}/{self.settings.linking_dir}"

    def _write_success_audit(
        self, job_id: str, request: IndexReadyRequest, chunks, *,
        artifact_snapshot, timings: dict[str, float], deleted_obsolete: int,
        activation, index_revision: str,
    ) -> list[str]:
        root = self._audit_root(request.document_id)
        version_root = f"{root}/versions/v{request.document_version}"
        chunks_payload = {
            "schema-version": "1.0",
            "document-id": request.document_id,
            "document-version": request.document_version,
            "index-revision": index_revision,
            "organization-id": request.organization_id,
            "wiki-id": request.wiki_id,
            "chunks": [
                {
                    "point-id": item.point_id, "content-id": item.content_id,
                    "index-revision": item.index_revision,
                    "content-type": item.content_type, "content-title": item.content_title,
                    "content-order": item.content_order,
                    "chunk-index": item.chunk_index, "page-start": item.page_start,
                    "page-end": item.page_end, "source-blocks": list(item.source_blocks),
                    "token-count": item.token_count, "content-hash": item.content_hash, "text": item.text,
                }
                for item in chunks
            ],
        }
        chunks_key = f"{version_root}/chunks.json"
        self.storage.upload_json(self.settings.minio_bucket, chunks_key, chunks_payload)
        manifest = {
            "schema-version": "1.0", "pipeline-version": self.settings.pipeline_version,
            "chunker-version": self.settings.chunker_version, "status": "succeeded",
            "job-id": job_id, "document-id": request.document_id,
            "document-version": request.document_version,
            "index-revision": index_revision,
            "organization-id": request.organization_id,
            "wiki-id": request.wiki_id, "collection": self.settings.qdrant_collection,
            "embedding-model": self.settings.embedding_model, "reranker-model": self.settings.reranker_model,
            "artifact": {
                "bucket": artifact_snapshot.bucket, "object-key": artifact_snapshot.object_key,
                "etag": artifact_snapshot.etag, "sha256": artifact_snapshot.sha256, "size": artifact_snapshot.size,
            },
            "chunk-count": len(chunks), "deleted-obsolete-points": deleted_obsolete,
            "activation": {
                "activated": activation.activated,
                "active-version": activation.active_version,
                "active-index-revision": activation.active_index_revision,
                "reason": activation.reason,
                "deactivated-points": activation.deactivated_points,
            },
            "timings": timings, "completed-time": utc_now(),
            "outputs": [chunks_key],
        }
        manifest_key = f"{version_root}/index-manifest.json"
        self.storage.upload_json(self.settings.minio_bucket, manifest_key, manifest)
        success_key = f"{root}/jobs/{job_id}/success.json"
        self.storage.upload_json(
            self.settings.minio_bucket, success_key,
            {"event": "document-linking-indexed", **manifest, "outputs": [chunks_key, manifest_key]},
        )
        return [chunks_key, manifest_key, success_key]

    def _write_failure_audit(self, job_id: str, request: IndexReadyRequest, exc: Exception) -> None:
        key = f"{self._audit_root(request.document_id)}/jobs/{job_id}/failure.json"
        try:
            self.storage.upload_json(
                self.settings.minio_bucket, key,
                {
                    "event": "document-linking-failed", "status": "failed", "job-id": job_id,
                    "document-id": request.document_id, "document-version": request.document_version,
                    "index-revision": job_id,
                    "error": {"type": type(exc).__name__, "message": str(exc)[:1000]},
                    "failure-time": utc_now(),
                },
            )
        except Exception:
            logger.exception("Could not write failure audit for job %s", job_id)

    def record_failure(self, job_id: str, exc: Exception) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        request = IndexReadyRequest.model_validate(job["payload"])
        self._write_failure_audit(job_id, request, exc)
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        self.jobs.update(
            job_id, status="failed", error=error, completed_time=utc_now()
        )
        delivered, callback_error = self.callbacks.send({
            "event": "document-linking-failed", "status": "failed", "job_id": job_id,
            "document_id": request.document_id,
            "document_version": request.document_version,
            "error": error,
        })
        return self.jobs.update(
            job_id, callback_delivered=delivered, callback_error=callback_error
        )

    def process(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        request = IndexReadyRequest.model_validate(job["payload"])
        lock_id = f"{request.organization_id}:{request.wiki_id}:{request.document_id}"
        if not self.jobs.acquire_lock(lock_id, job_id):
            raise RuntimeError("document-index-lock-busy")
        started = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            index_revision = job_id
            self.jobs.update(job_id, index_revision=index_revision)
            self.jobs.update(job_id, status="downloading")
            phase = time.perf_counter()
            artifact, snapshot = self.storage.download_verified(
                request.artifact.bucket, request.artifact.object_key,
                expected_etag=request.artifact.etag, expected_sha256=request.artifact.sha256,
            )
            timings["download_seconds"] = round(time.perf_counter() - phase, 6)

            self.jobs.update(job_id, status="parsing")
            phase = time.perf_counter()
            adapter = ArtifactAdapterFactory.for_artifact(
                request.artifact.object_key, request.artifact.content_type
            )
            context = AdapterContext(
                document_id=request.document_id, organization_id=request.organization_id, wiki_id=request.wiki_id,
                document_version=request.document_version, title=request.document.title,
                source_filename=request.document.source_filename, language=request.document.language,
                tags=tuple(request.document.tags), visibility=request.document.visibility,
                artifact_bucket=snapshot.bucket,
                artifact_key=snapshot.object_key, artifact_sha256=snapshot.sha256 or "",
                artifact_etag=snapshot.etag, extra_metadata=request.document.extra,
            )
            document = adapter.parse(artifact, context)
            timings["parsing_seconds"] = round(time.perf_counter() - phase, 6)

            self.jobs.update(job_id, status="chunking")
            phase = time.perf_counter()
            chunks = SemanticChunker(self.settings, self.embedder).chunk(
                document, index_revision=index_revision
            )
            if not chunks:
                raise ValueError("No chunks were generated from the published snapshot")
            timings["chunking_seconds"] = round(time.perf_counter() - phase, 6)

            self.jobs.update(job_id, status="embedding", chunk_count=len(chunks))
            phase = time.perf_counter()
            passages = [
                compose_semantic_passage(
                    content_title=item.content_title,
                    body=item.text,
                )
                for item in chunks
            ]
            embeddings = self.embedder.encode_documents(passages)
            if len(embeddings.dense) != len(chunks):
                raise RuntimeError("Embedding count does not match chunk count")
            embedded = []
            for index, chunk in enumerate(chunks):
                dense = embeddings.dense[index]
                if len(dense) != self.settings.qdrant_vector_size:
                    raise RuntimeError(
                        f"Embedding dimension {len(dense)} != configured {self.settings.qdrant_vector_size}"
                    )
                embedded.append(EmbeddedChunk(
                    chunk=chunk, dense=dense,
                    sparse_indices=embeddings.sparse_indices[index],
                    sparse_values=embeddings.sparse_values[index],
                ))
            timings["embedding_seconds"] = round(time.perf_counter() - phase, 6)

            self.jobs.update(job_id, status="indexing")
            phase = time.perf_counter()
            self.vector.ensure_collection()
            active_before = self.vector.get_active_state(
                request.organization_id, request.wiki_id, request.document_id
            )
            retrying_active_revision = active_before is not None and (
                active_before.document_version == request.document_version
                and active_before.index_revision == index_revision
            )
            indexed_at = utc_now()
            self.vector.upsert(
                embedded, embedding_model=self.settings.embedding_model,
                chunker_version=self.settings.chunker_version, indexed_at=indexed_at,
                version_state="active" if retrying_active_revision else "inactive",
            )
            obsolete = self.vector.delete_obsolete_points(
                request.organization_id, request.wiki_id, request.document_id,
                request.document_version, index_revision,
                [item.point_id for item in chunks],
            )

            # Verify the new revision before removing any previously searchable version.
            self.jobs.update(job_id, status="verifying")
            current_count = self.vector.count_revision(
                request.organization_id,
                request.wiki_id,
                request.document_id,
                request.document_version,
                index_revision,
            )
            if current_count != len(chunks):
                raise RuntimeError(f"Qdrant verification failed: expected {len(chunks)}, got {current_count}")

            activation = self.vector.activate_version(
                request.organization_id,
                request.wiki_id,
                request.document_id,
                request.document_version,
                index_revision,
                replace_existing=request.replace_existing,
                expected_point_count=len(chunks),
            )
            timings["qdrant_seconds"] = round(time.perf_counter() - phase, 6)
            timings["total_seconds"] = round(time.perf_counter() - started, 6)

            audit_outputs: list[str] = []
            audit_error: str | None = None
            if self.settings.write_audit_artifacts:
                try:
                    audit_outputs = self._write_success_audit(
                        job_id, request, chunks, artifact_snapshot=snapshot, timings=timings,
                        deleted_obsolete=obsolete, activation=activation,
                        index_revision=index_revision,
                    )
                except Exception as exc:
                    audit_error = str(exc)[:500]
                    logger.exception("Index succeeded but MinIO audit write failed job_id=%s", job_id)

            result = {
                "document_id": request.document_id, "document_version": request.document_version,
                "index_revision": index_revision,
                "organization_id": request.organization_id, "wiki_id": request.wiki_id,
                "collection": self.settings.qdrant_collection, "chunk_count": len(chunks),
                "deleted_obsolete_points": obsolete,
                "artifact_sha256": snapshot.sha256,
                "artifact_etag": snapshot.etag,
                "activated": activation.activated,
                "active_version": activation.active_version,
                "active_index_revision": activation.active_index_revision,
                "activation_reason": activation.reason,
                "deactivated_points": activation.deactivated_points,
                "audit_outputs": audit_outputs, "audit_error": audit_error, "timings": timings,
            }
            self.jobs.update(job_id, status="succeeded", result=result, error=None, completed_time=utc_now())
            delivered, callback_error = self.callbacks.send({
                "event": "document-linking-indexed", "status": "succeeded", "job_id": job_id,
                "document_id": request.document_id,
                "document_version": request.document_version,
                "result": result,
            })
            return self.jobs.update(
                job_id, callback_delivered=delivered, callback_error=callback_error
            )
        except Exception:
            logger.exception("Indexing attempt failed job_id=%s", job_id)
            raise
        finally:
            self.jobs.release_lock(lock_id, job_id)

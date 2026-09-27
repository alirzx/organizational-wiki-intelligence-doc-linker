from __future__ import annotations

import logging
import time

from app.core.config import Settings
from app.services.callbacks import CallbackClient
from app.services.job_store import JobStore, utc_now
from app.storage.minio_store import MinIOStore
from app.vector.qdrant_store import QdrantStore

logger = logging.getLogger(__name__)


class DeleteService:
    def __init__(self, settings: Settings, jobs: JobStore, storage: MinIOStore, vector: QdrantStore, callbacks: CallbackClient):
        self.settings = settings
        self.jobs = jobs
        self.storage = storage
        self.vector = vector
        self.callbacks = callbacks

    def record_failure(self, job_id: str, exc: Exception) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        p = job["payload"]
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        self.jobs.update(job_id, status="failed", error=error, completed_time=utc_now())
        root = f"{self.settings.document_prefix}/{p['document_id']}/{self.settings.linking_dir}"
        try:
            self.storage.upload_json(
                self.settings.minio_bucket, f"{root}/jobs/{job_id}/failure.json",
                {"event": "document-linking-delete-failed", "status": "failed", "job-id": job_id,
                 "document-id": p["document_id"], "error": error, "failure-time": utc_now()},
            )
        except Exception:
            logger.exception("Could not write delete failure audit")
        delivered, callback_error = self.callbacks.send({
            "event": "document-linking-delete-failed", "status": "failed",
            "job_id": job_id, "document_id": p["document_id"],
            "document_version": p.get("document_version"), "error": error,
        })
        return self.jobs.update(job_id, callback_delivered=delivered, callback_error=callback_error)

    def process(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        p = job["payload"]
        lock_id = f"{p['organization_id']}:{p['wiki_id']}:{p['document_id']}"
        if not self.jobs.acquire_lock(lock_id, job_id):
            raise RuntimeError("document-index-lock-busy")
        started = time.perf_counter()
        try:
            self.jobs.update(job_id, status="deleting")
            self.vector.ensure_collection()
            deleted = self.vector.delete_document(
                p["organization_id"], p["wiki_id"], p["document_id"], p.get("document_version")
            )
            generation = self.jobs.bump_document_generation(
                p["organization_id"], p["wiki_id"], p["document_id"]
            )
            result = {
                "document_id": p["document_id"], "document_version": p.get("document_version"),
                "organization_id": p["organization_id"], "wiki_id": p["wiki_id"],
                "deleted_points": deleted, "document_generation": generation,
                "duration_seconds": round(time.perf_counter() - started, 6),
            }
            root = f"{self.settings.document_prefix}/{p['document_id']}/{self.settings.linking_dir}"
            try:
                self.storage.upload_json(
                    self.settings.minio_bucket, f"{root}/jobs/{job_id}/success.json",
                    {"event": "document-linking-deleted", "status": "succeeded", "job-id": job_id, **result},
                )
            except Exception:
                logger.exception("Could not write delete audit")
            self.jobs.update(job_id, status="succeeded", result=result, error=None, completed_time=utc_now())
            delivered, error = self.callbacks.send({
                "event": "document-linking-deleted", "status": "succeeded", "job_id": job_id,
                "document_id": p["document_id"],
                "document_version": p.get("document_version"), "result": result,
            })
            return self.jobs.update(job_id, callback_delivered=delivered, callback_error=error)
        except Exception:
            logger.exception("Delete attempt failed job_id=%s", job_id)
            raise
        finally:
            self.jobs.release_lock(lock_id, job_id)

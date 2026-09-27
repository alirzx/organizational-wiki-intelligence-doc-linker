from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
import httpx

from app.api.schemas import (
    AcceptedJobResponse, DeleteRequest, IndexReadyRequest, SearchRequest, SearchResponse,
)
from app.core.config import Settings, get_settings
from app.core.ids import validate_safe_id
from app.runtime import get_jobs, get_retrieval, get_storage, get_vector
from app.services.job_store import JobStore

router = APIRouter()


def authenticate(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    token = settings.api_auth_token
    if not token:
        return
    bearer = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if x_api_key != token and bearer != token:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Invalid service token"})


def _enqueue_index(job_id: str) -> None:
    from app.jobs import index_document
    index_document.apply_async(args=[job_id], task_id=job_id)


def _enqueue_delete(job_id: str) -> None:
    from app.jobs import delete_document
    delete_document.apply_async(args=[job_id], task_id=job_id)


@router.post(
    "/api/v1/documents/index-ready", response_model=AcceptedJobResponse,
    status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(authenticate)],
)
def index_ready(
    request: IndexReadyRequest, jobs: JobStore = Depends(get_jobs)
) -> AcceptedJobResponse:
    settings = get_settings()
    if request.artifact.bucket != settings.minio_bucket:
        raise HTTPException(status_code=400, detail={"code": "invalid-bucket", "message": "Artifact must use configured Wiki bucket"})
    try:
        live_artifact = get_storage().stat(request.artifact.bucket, request.artifact.object_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"code": "artifact-not-found", "message": "Stage-2 artifact is not available"}) from exc
    if request.artifact.etag and request.artifact.etag.strip('"') != (live_artifact.etag or ""):
        raise HTTPException(status_code=409, detail={"code": "artifact-version-mismatch", "message": "Artifact ETag changed"})
    generation = jobs.document_generation(
        request.organization_id, request.wiki_id, request.document_id
    )
    identity_seed = "|".join([
        request.organization_id, request.wiki_id, request.document_id, request.document_version,
        str(generation), settings.pipeline_version, settings.chunker_version,
        settings.embedding_model, request.artifact.object_key,
        request.artifact.sha256 or live_artifact.etag or "unknown",
    ])
    identity = "index:" + hashlib.sha256(identity_seed.encode()).hexdigest()
    try:
        job, created = jobs.create_or_get(
            operation="index", identity=identity, payload=request.model_dump(mode="json")
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "job-store-unavailable", "message": str(exc)}) from exc
    if created:
        try:
            _enqueue_index(job["job_id"])
        except Exception as exc:
            jobs.update(job["job_id"], status="failed", error={"type": type(exc).__name__, "message": str(exc)[:500]})
            raise HTTPException(status_code=503, detail={"code": "enqueue-failed", "message": "Index job could not be queued"}) from exc
    return AcceptedJobResponse(job_id=job["job_id"], status=job["status"], start_time=job["start_time"])


@router.delete(
    "/api/v1/documents/{document_id}", response_model=AcceptedJobResponse,
    status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(authenticate)],
)
def remove_document(
    document_id: str, request: DeleteRequest, jobs: JobStore = Depends(get_jobs)
) -> AcceptedJobResponse:
    try:
        validate_safe_id(document_id, "document_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid-document-id", "message": str(exc)}) from exc
    payload = {"document_id": document_id, **request.model_dump(mode="json")}
    seed = request.request_id or uuid.uuid4().hex
    identity = "delete:" + hashlib.sha256(
        f"{request.organization_id}|{request.wiki_id}|{document_id}|{request.document_version or '*'}|{seed}".encode()
    ).hexdigest()
    job, created = jobs.create_or_get(operation="delete", identity=identity, payload=payload)
    if created:
        try:
            _enqueue_delete(job["job_id"])
        except Exception as exc:
            jobs.update(job["job_id"], status="failed", error={"type": type(exc).__name__, "message": str(exc)[:500]})
            raise HTTPException(status_code=503, detail={"code": "enqueue-failed", "message": "Delete job could not be queued"}) from exc
    return AcceptedJobResponse(job_id=job["job_id"], status=job["status"], start_time=job["start_time"])


@router.post(
    "/api/v1/search", response_model=SearchResponse, dependencies=[Depends(authenticate)]
)
def search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    if request.limit and request.limit > settings.search_max_limit:
        raise HTTPException(status_code=400, detail={"code": "limit-too-large", "message": "Requested limit exceeds configured maximum"})
    try:
        return get_retrieval().search(request)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "search-failed", "message": str(exc)[:500]}) from exc


@router.get("/api/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
def job_status(job_id: str, jobs: JobStore = Depends(get_jobs)) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "job-not-found", "message": "Job not found"})
    return job


@router.get("/health/live")
def live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready")
def ready(response: Response) -> dict:
    storage = get_storage()
    jobs = get_jobs()
    vector = get_vector()
    minio_ok = storage.healthcheck()
    redis_ok = jobs.healthcheck()
    qdrant_ok = vector.healthcheck()
    settings = get_settings()
    models_ok = True
    if settings.embedding_backend == "http" or settings.reranker_backend == "http":
        try:
            models_ok = httpx.get(
                f"{settings.model_service_url.rstrip('/')}/health/ready",
                timeout=5, trust_env=False,
            ).is_success
        except Exception:
            models_ok = False
    all_ok = minio_ok and redis_ok and qdrant_ok and models_ok
    if not all_ok:
        response.status_code = 503
    return {
        "status": "ready" if all_ok else "not-ready",
        "minio": minio_ok, "redis": redis_ok, "qdrant": qdrant_ok, "models": models_ok,
    }

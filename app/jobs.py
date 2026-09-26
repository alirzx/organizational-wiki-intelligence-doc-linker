from __future__ import annotations

import logging

from celery import Celery

from app.core.config import get_settings
from app.runtime import get_deleter, get_indexer

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "wiki_hami_doc_linker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_default_queue=settings.celery_queue,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
)


def _retry_or_fail(task, job_id: str, exc: Exception, service):
    jobs = service.jobs
    job = jobs.get(job_id)
    retry_count = int((job or {}).get("retry_count", 0)) + 1
    if job:
        jobs.update(job_id, status="retrying", retry_count=retry_count)
    if task.request.retries < settings.task_max_retries:
        countdown = settings.task_retry_backoff_seconds * (2 ** task.request.retries)
        raise task.retry(exc=exc, countdown=countdown, max_retries=settings.task_max_retries)
    logger.exception("Job %s failed after retries", job_id)
    return service.record_failure(job_id, exc)


@celery_app.task(bind=True, name="doc_linker.index_document")
def index_document(self, job_id: str):
    service = get_indexer()
    try:
        return service.process(job_id)
    except Exception as exc:
        return _retry_or_fail(self, job_id, exc, service)


@celery_app.task(bind=True, name="doc_linker.delete_document")
def delete_document(self, job_id: str):
    service = get_deleter()
    try:
        return service.process(job_id)
    except Exception as exc:
        return _retry_or_fail(self, job_id, exc, service)

app = celery_app

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import routes
from app.api.schemas import IndexReadyRequest
from app.core.config import Settings
from app.storage.minio_store import ObjectSnapshot


def request() -> IndexReadyRequest:
    return IndexReadyRequest.model_validate(
        {
            "document_id": "64",
            "organization_id": "org-1",
            "wiki_id": "wiki-main",
            "document_version": "2",
            "artifact": {
                "bucket": "media",
                "object_key": "documents/64/versions/v2/document.json",
                "sha256": "a" * 64,
            },
            "document": {"title": "Security Policy"},
        }
    )


class FakeJobs:
    def __init__(self):
        self.created = None
        self.existing = None

    def document_generation(self, *_args):
        return 3

    def create_or_get(self, **kwargs):
        self.created = kwargs
        if self.existing:
            return self.existing, False
        job = {
            "job_id": "job-1",
            "status": "queued",
            "start_time": "2026-10-13T00:00:00Z",
        }
        self.existing = job
        return job, True

    def update(self, job_id, **updates):
        return {"job_id": job_id, **updates}


def test_index_acceptance_persists_live_etag_and_returns_correlation(monkeypatch):
    jobs = FakeJobs()
    storage = SimpleNamespace(
        stat=lambda bucket, key: ObjectSnapshot(bucket, key, "accepted-etag", 321)
    )
    enqueued = []
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    monkeypatch.setattr(routes, "_enqueue_index", enqueued.append)

    response = routes.index_ready(request(), jobs=jobs)

    assert response.document_id == "64"
    assert response.document_version == "2"
    assert jobs.created["payload"]["artifact"]["etag"] == "accepted-etag"
    assert enqueued == ["job-1"]

    duplicate = routes.index_ready(request(), jobs=jobs)
    assert duplicate.job_id == "job-1"
    assert enqueued == ["job-1"]


def test_missing_published_snapshot_is_a_pre_job_404(monkeypatch):
    jobs = FakeJobs()

    def missing(*_args):
        raise FileNotFoundError

    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(routes, "get_storage", lambda: SimpleNamespace(stat=missing))
    with pytest.raises(HTTPException) as raised:
        routes.index_ready(request(), jobs=jobs)
    assert raised.value.status_code == 404
    assert raised.value.detail["code"] == "artifact-not-found"
    assert jobs.created is None


def test_wrong_bucket_is_rejected_before_stat(monkeypatch):
    payload = request().model_dump(mode="json")
    payload["artifact"]["bucket"] = "attacker-bucket"
    payload["artifact"]["uri"] = None
    invalid_bucket = IndexReadyRequest.model_validate(payload)
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(HTTPException) as raised:
        routes.index_ready(invalid_bucket, jobs=FakeJobs())
    assert raised.value.detail["code"] == "invalid-bucket"

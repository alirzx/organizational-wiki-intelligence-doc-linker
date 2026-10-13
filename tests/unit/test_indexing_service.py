import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models.embedding import MockEmbeddingService
from app.services.indexer import IndexingService
from app.storage.minio_store import ObjectSnapshot
from app.vector.qdrant_store import ActivationResult


def payload(version: str = "2") -> dict:
    return {
        "document_id": "64",
        "organization_id": "org-1",
        "wiki_id": "wiki-main",
        "document_version": version,
        "artifact": {
            "bucket": "media",
            "object_key": f"documents/64/versions/v{version}/document.json",
            "content_type": "application/json",
            "etag": f"etag-{version}",
        },
        "document": {
            "title": "Security Policy",
            "source_filename": "security-policy.pdf",
            "language": "fa",
            "tags": ["policy"],
        },
        "replace_existing": True,
    }


class FakeJobs:
    def __init__(self, request_payload=None):
        request_payload = request_payload or payload()
        self.job = {
            "job_id": "job-1",
            "document_id": request_payload["document_id"],
            "document_version": request_payload["document_version"],
            "status": "queued",
            "payload": request_payload,
        }
        self.statuses = []
        self.locked = False

    def get(self, _job_id):
        return dict(self.job)

    def update(self, _job_id, **updates):
        self.job.update(updates)
        if "status" in updates:
            self.statuses.append(updates["status"])
        return dict(self.job)

    def acquire_lock(self, _identity, _owner):
        self.locked = True
        return True

    def release_lock(self, _identity, _owner):
        self.locked = False


class FakeStorage:
    def __init__(self):
        self.uploads = []
        self.expected_etag = None

    def download_verified(self, bucket, object_key, *, expected_etag=None, expected_sha256=None):
        self.expected_etag = expected_etag
        data = json.dumps(
            {
                "document_id": "64",
                "document_version": 2,
                "content": [
                    {
                        "id": "stable-content-id",
                        "type": "section",
                        "title": "Policy",
                        "text": "Published policy text for semantic indexing.",
                    }
                ],
            }
        ).encode()
        return data, ObjectSnapshot(bucket, object_key, expected_etag, len(data), "a" * 64)

    def upload_json(self, bucket, key, data):
        self.uploads.append((bucket, key, data))
        return SimpleNamespace(bucket=bucket, object_key=key)


class FakeVector:
    def __init__(self, *, fail_at=None):
        self.fail_at = fail_at
        self.active = "1"
        self.upsert_state = None
        self.activation_calls = 0
        self.chunk_count = 0

    def ensure_collection(self):
        pass

    def upsert(self, chunks, **kwargs):
        if self.fail_at == "upsert":
            raise RuntimeError("qdrant upsert failed")
        self.upsert_state = kwargs["version_state"]
        self.chunk_count = len(chunks)

    def delete_obsolete_points(self, *_args):
        return 0

    def count_document_version(self, *_args):
        if self.fail_at == "verify":
            return 0
        return self.chunk_count

    def activate_version(self, _org, _wiki, _document, version, *, replace_existing):
        self.activation_calls += 1
        assert replace_existing
        self.active = version
        return ActivationResult(True, version, "activated", 1)


class FakeCallbacks:
    def __init__(self, delivered=True):
        self.delivered = delivered
        self.payloads = []

    def send(self, callback_payload):
        self.payloads.append(callback_payload)
        return self.delivered, None if self.delivered else "callback unavailable"


def service(*, vector=None, callbacks=None, jobs=None, storage=None):
    settings = Settings(
        _env_file=None,
        embedding_backend="mock",
        qdrant_vector_size=16,
        chunk_target_tokens=60,
        chunk_max_tokens=100,
        chunk_min_tokens=10,
        chunk_overlap_tokens=10,
    )
    jobs = jobs or FakeJobs()
    storage = storage or FakeStorage()
    vector = vector or FakeVector()
    callbacks = callbacks or FakeCallbacks()
    return (
        IndexingService(
            settings,
            jobs,
            storage,
            vector,
            MockEmbeddingService(settings),
            callbacks,
        ),
        jobs,
        storage,
        vector,
        callbacks,
    )


def test_indexing_lifecycle_verifies_before_activation_and_emits_correlated_callback():
    indexer, jobs, storage, vector, callbacks = service()
    result = indexer.process("job-1")

    assert vector.upsert_state == "inactive"
    assert vector.active == "2"
    assert jobs.statuses == [
        "downloading",
        "parsing",
        "chunking",
        "embedding",
        "indexing",
        "verifying",
        "succeeded",
    ]
    assert storage.expected_etag == "etag-2"
    assert result["result"]["activated"] is True
    callback = callbacks.payloads[-1]
    assert callback["job_id"] == "job-1"
    assert callback["document_id"] == "64"
    assert callback["document_version"] == "2"

    uploaded_keys = [key for _bucket, key, _data in storage.uploads]
    assert "documents/64/Linking/versions/v2/chunks.json" in uploaded_keys
    assert "documents/64/Linking/versions/v2/index-manifest.json" in uploaded_keys
    assert all(not key.startswith("documents/64/versions/") for key in uploaded_keys)


@pytest.mark.parametrize("failure", ["upsert", "verify"])
def test_qdrant_failure_never_changes_previous_active_version(failure):
    vector = FakeVector(fail_at=failure)
    indexer, _jobs, _storage, vector, _callbacks = service(vector=vector)
    with pytest.raises(RuntimeError):
        indexer.process("job-1")
    assert vector.active == "1"
    assert vector.activation_calls == 0


def test_callback_failure_does_not_roll_back_successful_activation():
    callbacks = FakeCallbacks(delivered=False)
    indexer, jobs, _storage, vector, _callbacks = service(callbacks=callbacks)
    result = indexer.process("job-1")
    assert vector.active == "2"
    assert result["status"] == "succeeded"
    assert result["callback_delivered"] is False
    assert jobs.job["result"]["active_version"] == "2"


def test_failure_callback_contains_job_document_version_and_error():
    indexer, _jobs, _storage, _vector, callbacks = service()
    result = indexer.record_failure("job-1", RuntimeError("indexing failed"))
    callback = callbacks.payloads[-1]
    assert callback == {
        "event": "document-linking-failed",
        "status": "failed",
        "job_id": "job-1",
        "document_id": "64",
        "document_version": "2",
        "error": {"type": "RuntimeError", "message": "indexing failed"},
    }
    assert result["status"] == "failed"

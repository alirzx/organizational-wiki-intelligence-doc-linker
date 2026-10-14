import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.core.ids import sha256_text
from app.models.embedding import MockEmbeddingService
from app.services.indexer import IndexingService
from app.storage.minio_store import ObjectSnapshot
from app.vector.qdrant_store import ActivationResult, ActiveRevision


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
    def __init__(self, request_payload=None, *, job_id="job-1"):
        request_payload = request_payload or payload()
        self.job = {
            "job_id": job_id,
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
                "id": 58,
                "title": "Published Security Policy",
                "content": {
                    "sections": {
                        "Policy": (
                            "Published policy before "
                            '<img src="data:image/png;base64,AAAA" /> '
                            "after https://example.com/policy."
                        )
                    },
                    "source_document_id": 64,
                },
                "version_number": 2,
                "source_document": 64,
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
        self.active_revision = "rev-old"
        self.upsert_state = None
        self.upsert_states = []
        self.index_revisions = []
        self.activation_calls = 0
        self.chunk_count = 0
        self.embedded = []

    def ensure_collection(self):
        pass

    def upsert(self, chunks, **kwargs):
        if self.fail_at == "upsert":
            raise RuntimeError("qdrant upsert failed")
        self.upsert_state = kwargs["version_state"]
        self.upsert_states.append(self.upsert_state)
        self.index_revisions.append({chunk.chunk.index_revision for chunk in chunks})
        self.chunk_count = len(chunks)
        self.embedded = chunks

    def get_active_state(self, *_args):
        if self.active is None:
            return None
        return ActiveRevision(self.active, self.active_revision, self.chunk_count or 1)

    def delete_obsolete_points(self, *_args):
        return 0

    def count_revision(self, *_args):
        if self.fail_at == "verify":
            return 0
        return self.chunk_count

    def activate_version(
        self,
        _org,
        _wiki,
        _document,
        version,
        index_revision,
        *,
        replace_existing,
        expected_point_count,
    ):
        self.activation_calls += 1
        assert replace_existing
        assert expected_point_count == self.chunk_count
        self.active = version
        self.active_revision = index_revision
        return ActivationResult(True, version, "activated", 1, index_revision)


class FakeCallbacks:
    def __init__(self, delivered=True):
        self.delivered = delivered
        self.payloads = []

    def send(self, callback_payload):
        self.payloads.append(callback_payload)
        return self.delivered, None if self.delivered else "callback unavailable"


class RecordingEmbeddingService(MockEmbeddingService):
    def __init__(self, settings):
        super().__init__(settings)
        self.document_batches = []

    def encode_documents(self, texts):
        self.document_batches.append(list(texts))
        return super().encode_documents(texts)


def service(
    *,
    vector=None,
    callbacks=None,
    jobs=None,
    storage=None,
    embedder_type=MockEmbeddingService,
):
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
    indexer = IndexingService(
        settings,
        jobs,
        storage,
        vector,
        embedder_type(settings),
        callbacks,
    )
    return (
        indexer,
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
    assert result["result"]["index_revision"] == "job-1"
    assert result["result"]["active_index_revision"] == "job-1"
    callback = callbacks.payloads[-1]
    assert callback["job_id"] == "job-1"
    assert callback["document_id"] == "64"
    assert callback["document_version"] == "2"

    uploaded_keys = [key for _bucket, key, _data in storage.uploads]
    assert "documents/64/Linking/versions/v2/chunks.json" in uploaded_keys
    assert "documents/64/Linking/versions/v2/index-manifest.json" in uploaded_keys
    assert all(not key.startswith("documents/64/versions/") for key in uploaded_keys)

    manifest = next(
        data for _bucket, key, data in storage.uploads
        if key.endswith("index-manifest.json")
    )
    assert manifest["index-revision"] == "job-1"


def test_embedding_uses_sanitized_title_aware_passage_while_storage_keeps_body():
    indexer, _jobs, storage, vector, _callbacks = service(
        embedder_type=RecordingEmbeddingService
    )
    indexer.process("job-1")

    passage = "Policy\n\nPublished policy before after https://example.com/policy."
    assert indexer.embedder.document_batches == [[passage]]
    stored = vector.embedded[0].chunk
    assert stored.content_title == "Policy"
    assert stored.content_id is None
    assert stored.text == "Published policy before after https://example.com/policy."
    assert not stored.text.startswith("Policy")
    assert stored.source_metadata == {"section_key": "Policy"}
    assert stored.content_hash == sha256_text(stored.text)
    assert stored.content_hash != sha256_text(passage)
    assert vector.embedded[0].dense == indexer.embedder._dense(passage)
    expected_sparse_indices, expected_sparse_values = indexer.embedder._sparse(passage)
    assert vector.embedded[0].sparse_indices == expected_sparse_indices
    assert vector.embedded[0].sparse_values == expected_sparse_values

    chunks_audit = next(
        data for _bucket, key, data in storage.uploads if key.endswith("chunks.json")
    )
    serialized = json.dumps(chunks_audit).casefold()
    assert "data:image" not in serialized
    assert ";base64," not in serialized
    assert "<img" not in serialized


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


def test_retry_reuses_job_id_as_revision_and_new_job_uses_new_revision():
    vector = FakeVector()
    indexer, _jobs, _storage, vector, _callbacks = service(vector=vector)
    indexer.process("job-1")
    indexer.process("job-1")

    assert vector.index_revisions == [{"job-1"}, {"job-1"}]
    assert vector.upsert_states == ["inactive", "active"]

    new_jobs = FakeJobs(job_id="job-2")
    new_indexer, *_ = service(vector=vector, jobs=new_jobs)
    new_indexer.process("job-2")
    assert vector.index_revisions[-1] == {"job-2"}


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

import io
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.storage.minio_store import MinIOStore


class Response(io.BytesIO):
    def close(self):
        pass

    def release_conn(self):
        pass


class FakeMinioClient:
    def __init__(self, data: bytes, stats: list[tuple[str, int]]):
        self.data = data
        self.stats = list(stats)

    def stat_object(self, _bucket, _key):
        etag, size = self.stats.pop(0) if len(self.stats) > 1 else self.stats[0]
        return SimpleNamespace(etag=etag, size=size)

    def get_object(self, _bucket, _key):
        return Response(self.data)


def store(data: bytes, stats: list[tuple[str, int]]) -> MinIOStore:
    instance = MinIOStore.__new__(MinIOStore)
    instance.settings = Settings(_env_file=None, max_artifact_bytes=1024)
    instance.client = FakeMinioClient(data, stats)
    return instance


def test_sha256_mismatch_fails_download():
    data = b"snapshot"
    with pytest.raises(ValueError, match="SHA256"):
        store(data, [("etag-a", len(data))]).download_verified(
            "media", "documents/64/versions/v2/document.json", expected_sha256="0" * 64
        )


def test_accepted_etag_changed_before_worker_download_fails():
    data = b"snapshot"
    with pytest.raises(ValueError, match="ETag"):
        store(data, [("etag-b", len(data))]).download_verified(
            "media", "documents/64/versions/v2/document.json", expected_etag="etag-a"
        )


def test_mutation_during_download_still_fails():
    data = b"snapshot"
    with pytest.raises(RuntimeError, match="changed"):
        store(data, [("etag-a", len(data)), ("etag-b", len(data))]).download_verified(
            "media", "documents/64/versions/v2/document.json", expected_etag="etag-a"
        )

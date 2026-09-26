from __future__ import annotations

import io
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from minio import Minio

from app.core.config import Settings
from app.core.ids import sha256_bytes


@dataclass(frozen=True)
class ObjectSnapshot:
    bucket: str
    object_key: str
    etag: str | None
    size: int
    sha256: str | None = None


class MinIOStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        endpoint = settings.minio_endpoint.strip().rstrip("/")
        secure = settings.minio_secure
        if "://" in endpoint:
            parsed = urlsplit(endpoint)
            if not parsed.hostname:
                raise ValueError("Invalid MinIO endpoint")
            endpoint = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
            secure = parsed.scheme.casefold() == "https"
        self.client = Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
        )

    def ensure_bucket(self) -> None:
        if self.client.bucket_exists(self.settings.minio_bucket):
            return
        if not self.settings.minio_auto_create_bucket:
            raise RuntimeError(f"MinIO bucket {self.settings.minio_bucket!r} does not exist")
        self.client.make_bucket(self.settings.minio_bucket)

    def healthcheck(self) -> bool:
        try:
            return self.client.bucket_exists(self.settings.minio_bucket)
        except Exception:
            return False

    @staticmethod
    def _normalize_etag(value: str | None) -> str:
        return (value or "").strip('"')

    def stat(self, bucket: str, object_key: str) -> ObjectSnapshot:
        info = self.client.stat_object(bucket, object_key)
        return ObjectSnapshot(
            bucket=bucket,
            object_key=object_key,
            etag=self._normalize_etag(getattr(info, "etag", None)),
            size=int(getattr(info, "size", 0) or 0),
        )

    def download_verified(
        self,
        bucket: str,
        object_key: str,
        *,
        expected_etag: str | None = None,
        expected_sha256: str | None = None,
    ) -> tuple[bytes, ObjectSnapshot]:
        if bucket != self.settings.minio_bucket:
            raise ValueError("Only the configured Wiki MinIO bucket is allowed")
        before = self.stat(bucket, object_key)
        if before.size <= 0:
            raise ValueError("Artifact is empty")
        if before.size > self.settings.max_artifact_bytes:
            raise ValueError(
                f"Artifact exceeds max_artifact_bytes: {before.size} > {self.settings.max_artifact_bytes}"
            )
        if expected_etag and self._normalize_etag(expected_etag) != before.etag:
            raise ValueError("Artifact ETag does not match request")
        response = self.client.get_object(bucket, object_key)
        try:
            data = response.read(self.settings.max_artifact_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        if len(data) > self.settings.max_artifact_bytes:
            raise ValueError("Artifact exceeded max size while downloading")
        digest = sha256_bytes(data)
        if expected_sha256 and digest.casefold() != expected_sha256.casefold():
            raise ValueError("Artifact SHA256 does not match request")
        after = self.stat(bucket, object_key)
        if before.etag != after.etag or before.size != after.size:
            raise RuntimeError("Artifact changed while it was being downloaded")
        return data, ObjectSnapshot(bucket, object_key, after.etag, after.size, digest)

    def upload_bytes(self, bucket: str, object_key: str, data: bytes, content_type: str) -> ObjectSnapshot:
        self.client.put_object(
            bucket, object_key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        info = self.stat(bucket, object_key)
        if info.size != len(data):
            raise IOError(f"Uploaded size mismatch for {object_key}")
        return ObjectSnapshot(bucket, object_key, info.etag, info.size, sha256_bytes(data))

    def upload_json(self, bucket: str, object_key: str, payload: dict) -> ObjectSnapshot:
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        return self.upload_bytes(bucket, object_key, data, "application/json; charset=utf-8")

    def object_exists(self, bucket: str, object_key: str) -> bool:
        try:
            self.stat(bucket, object_key)
            return True
        except Exception:
            return False

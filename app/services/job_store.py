from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import Settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class JobStore:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.redis = client or redis.Redis.from_url(settings.redis_url, decode_responses=True)

    def _job_key(self, job_id: str) -> str:
        return f"doc-linker:job:{job_id}"

    def _identity_key(self, identity: str) -> str:
        return f"doc-linker:identity:{identity}"

    def _generation_key(self, organization_id: str, wiki_id: str, document_id: str) -> str:
        return f"doc-linker:generation:{organization_id}:{wiki_id}:{document_id}"

    def healthcheck(self) -> bool:
        try:
            return bool(self.redis.ping())
        except Exception:
            return False

    def document_generation(self, organization_id: str, wiki_id: str, document_id: str) -> int:
        raw = self.redis.get(self._generation_key(organization_id, wiki_id, document_id))
        return int(raw or 0)

    def bump_document_generation(self, organization_id: str, wiki_id: str, document_id: str) -> int:
        key = self._generation_key(organization_id, wiki_id, document_id)
        with self.redis.pipeline() as pipe:
            pipe.incr(key)
            pipe.expire(key, self.settings.job_ttl_seconds)
            generation, _ = pipe.execute()
        return int(generation)

    def create_or_get(self, *, operation: str, identity: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        identity_key = self._identity_key(identity)
        while True:
            with self.redis.pipeline() as pipe:
                try:
                    pipe.watch(identity_key)
                    existing = pipe.get(identity_key)
                    if existing:
                        job = self.get(existing)
                        if job and job.get("status") not in {"failed"}:
                            pipe.unwatch()
                            return job, False

                    job_id = uuid.uuid4().hex
                    now = utc_now()
                    job = {
                        "job_id": job_id, "operation": operation, "status": "queued",
                        "start_time": now, "updated_time": now, "payload": payload,
                        "error": None, "result": None, "callback_delivered": False,
                        "callback_error": None, "retry_count": 0,
                    }
                    pipe.multi()
                    pipe.set(
                        self._job_key(job_id),
                        json.dumps(job, ensure_ascii=False),
                        ex=self.settings.job_ttl_seconds,
                    )
                    pipe.set(identity_key, job_id, ex=self.settings.job_ttl_seconds)
                    pipe.execute()
                    return job, True
                except redis.WatchError:
                    continue

    def get(self, job_id: str) -> dict[str, Any] | None:
        raw = self.redis.get(self._job_key(job_id))
        return json.loads(raw) if raw else None

    def update(self, job_id: str, **updates: Any) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.update(updates)
        job["updated_time"] = utc_now()
        self.redis.set(
            self._job_key(job_id), json.dumps(job, ensure_ascii=False), ex=self.settings.job_ttl_seconds
        )
        return job

    def acquire_lock(self, identity: str, owner: str) -> bool:
        return bool(self.redis.set(f"doc-linker:lock:{identity}", owner, nx=True, ex=self.settings.lock_ttl_seconds))

    def release_lock(self, identity: str, owner: str) -> None:
        key = f"doc-linker:lock:{identity}"
        with self.redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                if pipe.get(key) == owner:
                    pipe.multi()
                    pipe.delete(key)
                    pipe.execute()
                else:
                    pipe.unwatch()
            except Exception:
                pipe.reset()

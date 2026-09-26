from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.models.domain import CanonicalDocument


@dataclass(frozen=True)
class AdapterContext:
    document_id: str
    organization_id: str
    wiki_id: str
    document_version: str
    title: str
    source_filename: str | None
    language: str | None
    tags: tuple[str, ...]
    visibility: str
    acl_groups: tuple[str, ...]
    artifact_bucket: str
    artifact_key: str
    artifact_sha256: str
    artifact_etag: str | None
    stage2_job_id: str | None = None
    stage2_pipeline_version: str | None = None
    extra_metadata: dict[str, Any] | None = None


class ArtifactAdapter(ABC):
    @abstractmethod
    def parse(self, data: bytes, context: AdapterContext) -> CanonicalDocument:
        raise NotImplementedError

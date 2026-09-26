from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CanonicalSection:
    section_id: str
    type_id: int | None
    title: str
    text: str
    order: int
    page_start: int | None = None
    page_end: int | None = None
    source_blocks: tuple[int, ...] = ()
    confidence: float | None = None
    classifier: str | None = None


@dataclass(frozen=True)
class CanonicalDocument:
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
    stage2_job_id: str | None
    stage2_pipeline_version: str | None
    sections: tuple[CanonicalSection, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkRecord:
    point_id: str
    document_id: str
    organization_id: str
    wiki_id: str
    document_version: str
    document_title: str
    source_filename: str | None
    language: str | None
    tags: tuple[str, ...]
    visibility: str
    acl_groups: tuple[str, ...]
    artifact_bucket: str
    artifact_key: str
    artifact_sha256: str
    artifact_etag: str | None
    stage2_job_id: str | None
    stage2_pipeline_version: str | None
    section_id: str
    section_type_id: int | None
    section_title: str
    section_order: int
    page_start: int | None
    page_end: int | None
    source_blocks: tuple[int, ...]
    section_confidence: float | None
    section_classifier: str | None
    chunk_index: int
    text: str
    content_hash: str
    token_count: int
    document_metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self, *, embedding_model: str, chunker_version: str, indexed_at: str) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "wiki_id": self.wiki_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "document_title": self.document_title,
            "source_filename": self.source_filename,
            "language": self.language,
            "tags": list(self.tags),
            "visibility": self.visibility,
            "acl_groups": list(self.acl_groups),
            "artifact_bucket": self.artifact_bucket,
            "artifact_key": self.artifact_key,
            "artifact_sha256": self.artifact_sha256,
            "artifact_etag": self.artifact_etag,
            "stage2_job_id": self.stage2_job_id,
            "stage2_pipeline_version": self.stage2_pipeline_version,
            "section_id": self.section_id,
            "section_type_id": self.section_type_id,
            "section_title": self.section_title,
            "section_order": self.section_order,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "source_blocks": list(self.source_blocks),
            "section_confidence": self.section_confidence,
            "section_classifier": self.section_classifier,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "embedding_model": embedding_model,
            "chunker_version": chunker_version,
            "indexed_at": indexed_at,
            "document_metadata": self.document_metadata,
        }


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: ChunkRecord
    dense: list[float]
    sparse_indices: list[int] = field(default_factory=list)
    sparse_values: list[float] = field(default_factory=list)

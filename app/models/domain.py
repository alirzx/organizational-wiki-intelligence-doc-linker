from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_SEMANTIC_TITLE_CHARS = 512


def compose_semantic_passage(*, content_title: object, body: object) -> str:
    """Build the shared title-aware input used for embedding and reranking.

    The canonical body remains separate in Qdrant and search responses. The
    bounded title prefix supplies section structure only to the model-facing
    representation.
    """

    title = str(content_title or "").strip()[:MAX_SEMANTIC_TITLE_CHARS]
    text = str(body or "").strip()
    if title and text:
        return f"{title}\n\n{text}"
    return text or title


@dataclass(frozen=True)
class CanonicalContent:
    """One ordered semantic unit from a source document.

    The generic content fields are authoritative. The optional legacy fields
    retain useful Stage-2 extraction provenance without requiring it for a
    Backend-published snapshot.
    """

    content_id: str | None
    content_type: str
    content_title: str
    text: str
    content_order: int
    source_metadata: dict[str, Any] = field(default_factory=dict)
    section_type_id: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_blocks: tuple[int, ...] = ()
    section_confidence: float | None = None
    section_classifier: str | None = None

    @property
    def section_id(self) -> str | None:
        return self.content_id

    @property
    def type_id(self) -> int | None:
        return self.section_type_id

    @property
    def title(self) -> str:
        return self.content_title

    @property
    def order(self) -> int:
        return self.content_order

    @property
    def confidence(self) -> float | None:
        return self.section_confidence

    @property
    def classifier(self) -> str | None:
        return self.section_classifier


# Compatibility import for callers that still use the Stage-2 domain name.
CanonicalSection = CanonicalContent


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
    source_kind: str
    contents: tuple[CanonicalContent, ...]
    source_metadata: dict[str, Any] = field(default_factory=dict)
    stage2_job_id: str | None = None
    stage2_pipeline_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sections(self) -> tuple[CanonicalContent, ...]:
        """Compatibility view for the existing chunker/legacy adapter API."""

        return self.contents


@dataclass(frozen=True)
class ChunkRecord:
    point_id: str
    document_id: str
    organization_id: str
    wiki_id: str
    document_version: str
    index_revision: str
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
    source_kind: str
    content_id: str | None
    content_type: str
    content_title: str
    content_order: int
    source_metadata: dict[str, Any]
    stage2_job_id: str | None
    stage2_pipeline_version: str | None
    section_type_id: int | None
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

    def payload(
        self,
        *,
        embedding_model: str,
        chunker_version: str,
        indexed_at: str,
        version_state: str,
    ) -> dict[str, Any]:
        payload = {
            "organization_id": self.organization_id,
            "wiki_id": self.wiki_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "index_revision": self.index_revision,
            "version_state": version_state,
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
            "source_kind": self.source_kind,
            "content_id": self.content_id,
            "content_type": self.content_type,
            "content_title": self.content_title,
            "content_order": self.content_order,
            "source_metadata": self.source_metadata,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "embedding_model": embedding_model,
            "chunker_version": chunker_version,
            "indexed_at": indexed_at,
            "document_metadata": self.document_metadata,
        }
        if self.source_kind == "legacy_stage2":
            payload.update({
                "stage2_job_id": self.stage2_job_id,
                "stage2_pipeline_version": self.stage2_pipeline_version,
                "section_id": self.content_id,
                "section_type_id": self.section_type_id,
                "section_title": self.content_title,
                "section_order": self.content_order,
                "page_start": self.page_start,
                "page_end": self.page_end,
                "source_blocks": list(self.source_blocks),
                "section_confidence": self.section_confidence,
                "section_classifier": self.section_classifier,
            })
        return payload


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: ChunkRecord
    dense: list[float]
    sparse_indices: list[int] = field(default_factory=list)
    sparse_values: list[float] = field(default_factory=list)

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.ids import validate_safe_id


class ArtifactRef(BaseModel):
    bucket: str = "media"
    object_key: str = Field(min_length=1, max_length=2048)
    uri: str | None = None
    content_type: str | None = None
    etag: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{64}$")

    @field_validator("object_key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("object_key must be a safe MinIO object key")
        return value

    @model_validator(mode="after")
    def validate_uri(self):
        if self.uri:
            expected = f"s3://{self.bucket}/{self.object_key}"
            if self.uri != expected:
                raise ValueError(f"uri must match bucket/object_key exactly: {expected}")
        return self


class ManifestRef(BaseModel):
    bucket: str = "media"
    object_key: str = Field(min_length=1, max_length=2048)
    etag: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{64}$")


class DocumentMetadata(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    source_filename: str | None = Field(default=None, max_length=1000)
    language: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=100)
    visibility: str = Field(default="internal", max_length=64)
    acl_groups: list[str] = Field(default_factory=list, max_length=200)
    extra: dict[str, Any] = Field(default_factory=dict)


class IndexReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=192)
    organization_id: str = Field(min_length=1, max_length=192)
    wiki_id: str = Field(min_length=1, max_length=192)
    document_version: str = Field(default="1", min_length=1, max_length=192)
    artifact: ArtifactRef
    manifest: ManifestRef | None = None
    document: DocumentMetadata
    replace_existing: bool = True

    @field_validator("document_id", "organization_id", "wiki_id", "document_version")
    @classmethod
    def safe_ids(cls, value: str, info) -> str:
        return validate_safe_id(value, info.field_name)

    @model_validator(mode="after")
    def validate_artifact_scope(self):
        expected = f"documents/{self.document_id}/Sectioning/"
        if not self.artifact.object_key.startswith(expected):
            raise ValueError("artifact must be a Stage-2 Sectioning object for document_id")
        artifact_name = self.artifact.object_key.rsplit("/", 1)[-1].casefold()
        if artifact_name not in {"sections.json", "sections.docx", "sections.md"}:
            raise ValueError("artifact must be sections.json, sections.docx, or sections.md")
        if self.manifest is not None:
            if self.manifest.bucket != self.artifact.bucket:
                raise ValueError("manifest and artifact must use the same MinIO bucket")
            expected_manifest = self.artifact.object_key.rsplit("/", 1)[0] + "/manifest.json"
            if self.manifest.object_key != expected_manifest:
                raise ValueError("manifest must be the manifest.json beside the Stage-2 artifact")
        return self


class AcceptedJobResponse(BaseModel):
    job_id: str
    status: str
    start_time: str


class SearchFilters(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=500)
    section_type_ids: list[int] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=50)
    language: str | None = None
    visibility: list[str] = Field(default_factory=list, max_length=20)
    acl_groups: list[str] = Field(default_factory=list, max_length=100)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    organization_id: str = Field(min_length=1, max_length=192)
    wiki_id: str = Field(min_length=1, max_length=192)
    limit: int | None = Field(default=None, ge=1, le=100)
    candidate_limit: int | None = Field(default=None, ge=5, le=500)
    mode: Literal["dense", "hybrid"] | None = None
    filters: SearchFilters = Field(default_factory=SearchFilters)

    @field_validator("organization_id", "wiki_id")
    @classmethod
    def safe_ids(cls, value: str, info) -> str:
        return validate_safe_id(value, info.field_name)


class SearchResult(BaseModel):
    rank: int
    score: float
    retrieval_score: float
    rerank_score: float | None
    text: str
    document_id: str
    document_version: str
    document_title: str
    source_filename: str | None = None
    section_id: str
    section_type_id: int | None = None
    section_title: str
    section_order: int
    section_confidence: float | None = None
    section_classifier: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_blocks: list[int] = Field(default_factory=list)
    chunk_index: int
    artifact_bucket: str
    artifact_key: str
    content_hash: str
    document_metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    mode: str
    candidate_count: int
    results: list[SearchResult]
    timings: dict[str, float]


class DeleteRequest(BaseModel):
    organization_id: str
    wiki_id: str
    document_version: str | None = None
    request_id: str | None = Field(default=None, max_length=192)

    @field_validator("organization_id", "wiki_id")
    @classmethod
    def safe_ids(cls, value: str, info) -> str:
        return validate_safe_id(value, info.field_name)

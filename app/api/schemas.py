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
    document_version: str = Field(min_length=1, max_length=192)
    artifact: ArtifactRef
    document: DocumentMetadata
    replace_existing: bool = True

    @field_validator("document_id", "organization_id", "wiki_id")
    @classmethod
    def safe_ids(cls, value: str, info) -> str:
        return validate_safe_id(value, info.field_name)

    @field_validator("document_version")
    @classmethod
    def positive_numeric_version(cls, value: str) -> str:
        if not value.isascii() or not value.isdigit() or value.startswith("0"):
            raise ValueError("document_version must be a positive decimal integer string")
        if int(value) <= 0:
            raise ValueError("document_version must be greater than zero")
        return value

    @model_validator(mode="after")
    def validate_artifact_scope(self):
        expected = (
            f"documents/{self.document_id}/versions/"
            f"v{self.document_version}/document.json"
        )
        if self.artifact.object_key != expected:
            raise ValueError(f"artifact object_key must be exactly {expected}")
        return self


class AcceptedJobResponse(BaseModel):
    job_id: str
    document_id: str | None = None
    document_version: str | None = None
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
    content_id: str | None = None
    content_type: str = "content"
    content_title: str
    content_order: int
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    section_id: str | None = None
    section_type_id: int | None = None
    section_title: str | None = None
    section_order: int | None = None
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

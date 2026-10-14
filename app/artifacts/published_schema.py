from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
StrictText = Annotated[str, Field(strict=True)]


def _sorted_mapping(value: dict[str, Any]) -> dict[str, Any]:
    """Return JSON metadata with deterministic mapping key order."""

    output: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, dict):
            output[key] = _sorted_mapping(item)
        elif isinstance(item, list):
            output[key] = [
                _sorted_mapping(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            output[key] = item
    return output


class PublishedContentSchema(BaseModel):
    """The RAG-bearing portion of Backend's immutable published snapshot."""

    model_config = ConfigDict(extra="allow")

    sections: dict[StrictText, StrictText]
    source_document_id: PositiveStrictInt


class PublishedDocumentSchema(BaseModel):
    """Authoritative Backend ``document.json`` production contract."""

    model_config = ConfigDict(extra="allow")

    # Title is absent in a few historical snapshots, so the request title is a
    # documented fallback. Identity and knowledge-bearing fields remain strict.
    title: StrictText | None = None
    content: PublishedContentSchema
    version_number: PositiveStrictInt
    source_document: PositiveStrictInt

    id: PositiveStrictInt | None = None
    position: StrictText | None = None
    owners: list[PositiveStrictInt] = Field(default_factory=list)
    status: StrictText | None = None
    source_version: PositiveStrictInt | None = None
    published_at: StrictText | None = None
    available_actions: dict[str, Any] = Field(default_factory=dict)
    hyperlink_rebuild: dict[str, Any] = Field(default_factory=dict)
    created_at: StrictText | None = None
    updated_at: StrictText | None = None

    def backend_metadata(self) -> dict[str, Any]:
        """Return non-semantic Backend metadata under stable field names."""

        metadata: dict[str, Any] = {
            "wiki_document_id": self.id,
            "source_document_id": self.content.source_document_id,
            "position": self.position,
            "owners": list(self.owners),
            "status": self.status,
            "version_number": self.version_number,
            "source_document": self.source_document,
            "source_version": self.source_version,
            "published_at": self.published_at,
            "available_actions": _sorted_mapping(self.available_actions),
            "hyperlink_rebuild": _sorted_mapping(self.hyperlink_rebuild),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.content.model_extra:
            metadata["content_extra"] = _sorted_mapping(self.content.model_extra)
        if self.model_extra:
            metadata["extra"] = _sorted_mapping(self.model_extra)
        return metadata

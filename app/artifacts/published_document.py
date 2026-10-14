from __future__ import annotations

import json

from pydantic import ValidationError

from app.artifacts.base import AdapterContext, ArtifactAdapter
from app.artifacts.cleaning import clean_text
from app.artifacts.published_schema import PublishedDocumentSchema
from app.models.domain import CanonicalContent, CanonicalDocument


class PublishedDocumentAdapter(ArtifactAdapter):
    """Map Backend's immutable published snapshot into the RAG domain."""

    def parse(self, data: bytes, context: AdapterContext) -> CanonicalDocument:
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("published document.json must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("published document.json must be an object")

        try:
            snapshot = PublishedDocumentSchema.model_validate(payload)
        except ValidationError as exc:
            errors = []
            for error in exc.errors(include_url=False, include_input=False)[:8]:
                location = ".".join(str(part) for part in error["loc"])
                errors.append(f"{location}: {error['msg']}")
            raise ValueError(
                "published document.json schema is invalid: " + "; ".join(errors)
            ) from exc
        self._validate_identity(snapshot, context)

        contents: list[CanonicalContent] = []
        for position, (section_key, section_body) in enumerate(
            snapshot.content.sections.items(), start=1
        ):
            title = clean_text(section_key)
            if not title:
                raise ValueError("published document.json contains an empty section key")
            text = clean_text(section_body)
            if not text:
                continue
            contents.append(
                CanonicalContent(
                    content_id=None,
                    content_type="section",
                    content_title=title,
                    text=text,
                    content_order=position,
                    source_metadata={"section_key": section_key},
                )
            )
        if not contents:
            raise ValueError("published document.json contains no usable text content")

        snapshot_title = clean_text(snapshot.title or "")
        canonical_title = snapshot_title or clean_text(context.title)
        return CanonicalDocument(
            document_id=context.document_id,
            organization_id=context.organization_id,
            wiki_id=context.wiki_id,
            document_version=context.document_version,
            title=canonical_title,
            source_filename=context.source_filename,
            language=context.language,
            tags=context.tags,
            visibility=context.visibility,
            artifact_bucket=context.artifact_bucket,
            artifact_key=context.artifact_key,
            artifact_sha256=context.artifact_sha256,
            artifact_etag=context.artifact_etag,
            source_kind="published_snapshot",
            contents=tuple(contents),
            source_metadata={},
            metadata={
                "backend_snapshot": snapshot.backend_metadata(),
                "request_extra": dict(context.extra_metadata or {}),
            },
        )

    @staticmethod
    def _validate_identity(
        snapshot: PublishedDocumentSchema, context: AdapterContext
    ) -> None:
        source_matches = str(snapshot.source_document) == context.document_id
        content_source_matches = (
            str(snapshot.content.source_document_id) == context.document_id
        )
        if not source_matches and content_source_matches:
            raise ValueError(
                "published document.json source_document does not match request document_id"
            )
        if source_matches and not content_source_matches:
            raise ValueError(
                "published document.json content.source_document_id does not match "
                "request document_id"
            )
        if snapshot.source_document != snapshot.content.source_document_id:
            raise ValueError(
                "published document.json source_document and "
                "content.source_document_id disagree"
            )
        if not source_matches:
            raise ValueError(
                "published document.json source_document does not match request document_id"
            )
        if str(snapshot.version_number) != context.document_version:
            raise ValueError(
                "published document.json version_number does not match request document_version"
            )

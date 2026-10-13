from __future__ import annotations

import json
from typing import Any

from app.artifacts.base import AdapterContext, ArtifactAdapter
from app.artifacts.cleaning import clean_text
from app.models.domain import CanonicalContent, CanonicalDocument


class PublishedDocumentAdapter(ArtifactAdapter):
    """Map the Backend's immutable published JSON snapshot into the RAG domain.

    The Backend snapshot serializer is not present in this repository.  Schema
    tolerance is deliberately isolated here: a snapshot may contain an ordered
    ``content``/``contents``/``sections``/``blocks`` list, a text ``content``,
    or a nested ``document`` object using those fields.  Stable IDs are copied
    when present and are never synthesized as source references.
    """

    def parse(self, data: bytes, context: AdapterContext) -> CanonicalDocument:
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("published document.json must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("published document.json must be an object")

        body = payload.get("document")
        if not isinstance(body, dict):
            body = payload
        self._validate_identity(payload, body, context)

        raw_content = self._content_value(body)
        if isinstance(raw_content, str):
            raw_items: list[Any] = [{"text": raw_content}]
        elif isinstance(raw_content, list):
            raw_items = raw_content
        else:
            raise TypeError(
                "published document.json must contain ordered content, contents, sections, or blocks"
            )

        contents: list[CanonicalContent] = []
        for position, raw in enumerate(raw_items, start=1):
            item = raw if isinstance(raw, dict) else {"text": str(raw)}
            text = clean_text(self._text(item))
            if not text:
                continue
            source_metadata = item.get("metadata")
            if not isinstance(source_metadata, dict):
                source_metadata = {}
            attrs = item.get("attrs")
            if isinstance(attrs, dict):
                source_metadata = {**attrs, **source_metadata}
            content_id = self._first_string(
                item,
                "content_id",
                "content-id",
                "section_id",
                "section-id",
                "block_id",
                "block-id",
                "node_id",
                "node-id",
                "id",
            )
            if content_id is None and isinstance(attrs, dict):
                content_id = self._first_string(attrs, "id", "content_id", "block_id", "node_id")
            content_type = self._first_string(item, "content_type", "content-type", "type") or "content"
            title = self._first_string(item, "content_title", "content-title", "title", "heading") or ""
            contents.append(
                CanonicalContent(
                    content_id=content_id,
                    content_type=content_type,
                    content_title=clean_text(title),
                    text=text,
                    content_order=self._positive_int(item.get("order")) or position,
                    source_metadata=source_metadata,
                )
            )
        if not contents:
            raise ValueError("published document.json contains no usable text content")

        source_metadata = payload.get("metadata")
        if not isinstance(source_metadata, dict):
            source_metadata = {}
        return CanonicalDocument(
            document_id=context.document_id,
            organization_id=context.organization_id,
            wiki_id=context.wiki_id,
            document_version=context.document_version,
            title=context.title,
            source_filename=context.source_filename,
            language=context.language,
            tags=context.tags,
            visibility=context.visibility,
            acl_groups=context.acl_groups,
            artifact_bucket=context.artifact_bucket,
            artifact_key=context.artifact_key,
            artifact_sha256=context.artifact_sha256,
            artifact_etag=context.artifact_etag,
            source_kind="published_snapshot",
            contents=tuple(sorted(contents, key=lambda item: item.content_order)),
            source_metadata=source_metadata,
            metadata=context.extra_metadata or {},
        )

    @staticmethod
    def _content_value(body: dict[str, Any]) -> Any:
        for key in ("content", "contents", "sections", "blocks"):
            if key in body:
                return body[key]
        return None

    @classmethod
    def _validate_identity(
        cls, payload: dict[str, Any], body: dict[str, Any], context: AdapterContext
    ) -> None:
        document_id = cls._first_value(payload, body, "document_id", "document-id")
        if document_id is not None and str(document_id) != context.document_id:
            raise ValueError("published document.json document_id does not match request")
        version = cls._first_value(
            payload, body, "document_version", "document-version", "version_number"
        )
        if version is not None and str(version) != context.document_version:
            raise ValueError("published document.json document_version does not match request")

    @classmethod
    def _text(cls, item: dict[str, Any]) -> str:
        direct = item.get("text")
        if isinstance(direct, str):
            return direct
        nested = item.get("content")
        if not isinstance(nested, list):
            return ""
        parts: list[str] = []
        for child in nested:
            if isinstance(child, dict):
                value = cls._text(child)
                if value:
                    parts.append(value)
            elif isinstance(child, str):
                parts.append(child)
        return "\n".join(parts)

    @staticmethod
    def _first_value(
        primary: dict[str, Any], secondary: dict[str, Any], *keys: str
    ) -> Any:
        for source in (primary, secondary):
            for key in keys:
                if source.get(key) is not None:
                    return source[key]
        return None

    @staticmethod
    def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return None

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

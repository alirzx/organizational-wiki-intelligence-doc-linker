from __future__ import annotations

import json

from app.artifacts.base import AdapterContext, ArtifactAdapter
from app.artifacts.cleaning import clean_text
from app.models.domain import CanonicalDocument, CanonicalSection


class SectionsJsonAdapter(ArtifactAdapter):
    def parse(self, data: bytes, context: AdapterContext) -> CanonicalDocument:
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("sections.json must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
            raise ValueError("sections.json must contain a sections array")
        payload_document_id = payload.get("document-id") or payload.get("document_id")
        if payload_document_id is not None and str(payload_document_id) != context.document_id:
            raise ValueError("sections.json document ID does not match request")

        sections: list[CanonicalSection] = []
        for position, raw in enumerate(payload["sections"], start=1):
            if not isinstance(raw, dict):
                continue
            text = clean_text(str(raw.get("text") or ""))
            if not text:
                continue
            source_blocks = raw.get("source-blocks", raw.get("source_blocks", []))
            if not isinstance(source_blocks, list):
                source_blocks = []
            section_id = str(
                raw.get("section-id") or raw.get("section_id") or f"section-{position:03d}"
            )
            sections.append(
                CanonicalSection(
                    section_id=section_id,
                    type_id=_as_int(raw.get("type-id", raw.get("type_id"))),
                    title=clean_text(str(raw.get("title") or section_id)),
                    text=text,
                    order=_as_int(raw.get("order")) or position,
                    page_start=_as_int(raw.get("page-start", raw.get("page_start"))),
                    page_end=_as_int(raw.get("page-end", raw.get("page_end"))),
                    source_blocks=tuple(x for x in (_as_int(v) for v in source_blocks) if x is not None),
                    confidence=_as_float(raw.get("confidence")),
                    classifier=str(raw.get("classifier")) if raw.get("classifier") else None,
                )
            )
        if not sections:
            raise ValueError("sections.json contains no usable text sections")
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
            stage2_job_id=context.stage2_job_id or _optional_str(payload.get("job-id")),
            stage2_pipeline_version=context.stage2_pipeline_version,
            sections=tuple(sorted(sections, key=lambda item: item.order)),
            metadata=context.extra_metadata or {},
        )


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value):
    return str(value) if value is not None else None

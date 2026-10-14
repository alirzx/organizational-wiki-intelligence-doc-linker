from __future__ import annotations

import re

from app.artifacts.base import AdapterContext, ArtifactAdapter
from app.artifacts.cleaning import clean_text
from app.models.domain import CanonicalContent, CanonicalDocument

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class MarkdownAdapter(ArtifactAdapter):
    def parse(self, data: bytes, context: AdapterContext) -> CanonicalDocument:
        try:
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Markdown artifact must be UTF-8") from exc
        raw = clean_text(raw)
        sections: list[CanonicalContent] = []
        title = "Document"
        buffer: list[str] = []

        def flush() -> None:
            nonlocal buffer, title
            text = clean_text("\n".join(buffer))
            if text:
                order = len(sections) + 1
                sections.append(
                    CanonicalContent(
                        content_id=f"section-{order:03d}",
                        content_type="section",
                        content_title=title,
                        text=text,
                        content_order=order,
                    )
                )
            buffer = []

        for line in raw.splitlines():
            match = HEADING_RE.match(line)
            if match:
                flush()
                title = clean_text(match.group(2)) or "Section"
                continue
            buffer.append(line)
        flush()
        if not sections:
            raise ValueError("Markdown contains no usable text")
        return CanonicalDocument(
            document_id=context.document_id, organization_id=context.organization_id, wiki_id=context.wiki_id,
            document_version=context.document_version, title=context.title, source_filename=context.source_filename,
            language=context.language, tags=context.tags, visibility=context.visibility,
            artifact_bucket=context.artifact_bucket, artifact_key=context.artifact_key,
            artifact_sha256=context.artifact_sha256, artifact_etag=context.artifact_etag,
            source_kind="legacy_stage2", contents=tuple(sections), source_metadata={},
            stage2_job_id=context.stage2_job_id, stage2_pipeline_version=context.stage2_pipeline_version,
            metadata=context.extra_metadata or {},
        )

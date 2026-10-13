from __future__ import annotations

from pathlib import PurePosixPath

from app.artifacts.base import ArtifactAdapter
from app.artifacts.docx import DocxAdapter
from app.artifacts.markdown import MarkdownAdapter
from app.artifacts.published_document import PublishedDocumentAdapter
from app.artifacts.sections_json import SectionsJsonAdapter


class ArtifactAdapterFactory:
    @staticmethod
    def for_artifact(object_key: str, content_type: str | None = None) -> ArtifactAdapter:
        path = PurePosixPath(object_key)
        suffix = path.suffix.casefold()
        content = (content_type or "").casefold()
        parts = path.parts
        if (
            len(parts) == 5
            and parts[0] == "documents"
            and parts[2] == "versions"
            and parts[3].startswith("v")
            and parts[4] == "document.json"
        ):
            return PublishedDocumentAdapter()
        if path.name.casefold() == "sections.json":
            return SectionsJsonAdapter()
        if suffix == ".docx" or "officedocument.wordprocessingml" in content:
            return DocxAdapter()
        if suffix in {".md", ".markdown"} or "markdown" in content:
            return MarkdownAdapter()
        raise ValueError(f"Unsupported artifact type: {suffix or content_type or 'unknown'}")

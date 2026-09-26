from __future__ import annotations

from pathlib import PurePosixPath

from app.artifacts.base import ArtifactAdapter
from app.artifacts.docx import DocxAdapter
from app.artifacts.markdown import MarkdownAdapter
from app.artifacts.sections_json import SectionsJsonAdapter


class ArtifactAdapterFactory:
    @staticmethod
    def for_artifact(object_key: str, content_type: str | None = None) -> ArtifactAdapter:
        suffix = PurePosixPath(object_key).suffix.casefold()
        content = (content_type or "").casefold()
        if suffix == ".json" or "json" in content:
            return SectionsJsonAdapter()
        if suffix == ".docx" or "officedocument.wordprocessingml" in content:
            return DocxAdapter()
        if suffix in {".md", ".markdown"} or "markdown" in content:
            return MarkdownAdapter()
        raise ValueError(f"Unsupported Stage-2 artifact type: {suffix or content_type or 'unknown'}")

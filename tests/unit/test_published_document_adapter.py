import json

import pytest

from app.artifacts.base import AdapterContext
from app.artifacts.factory import ArtifactAdapterFactory
from app.artifacts.published_document import PublishedDocumentAdapter
from app.artifacts.sections_json import SectionsJsonAdapter


def context() -> AdapterContext:
    return AdapterContext(
        document_id="64",
        organization_id="org-1",
        wiki_id="wiki-main",
        document_version="2",
        title="Security Policy",
        source_filename="security-policy.pdf",
        language="fa",
        tags=("policy",),
        visibility="internal",
        acl_groups=(),
        artifact_bucket="media",
        artifact_key="documents/64/versions/v2/document.json",
        artifact_sha256="a" * 64,
        artifact_etag="etag-2",
    )


def test_published_document_preserves_stable_content_references():
    payload = {
        "document_id": "64",
        "document_version": 2,
        "metadata": {"editor": "wiki"},
        "content": [
            {
                "id": "backend-section-9",
                "type": "section",
                "title": "Access",
                "order": 4,
                "text": "Only authorized staff may enter.",
                "metadata": {"slug": "access"},
            }
        ],
    }
    document = PublishedDocumentAdapter().parse(json.dumps(payload).encode(), context())
    content = document.contents[0]
    assert document.source_kind == "published_snapshot"
    assert content.content_id == "backend-section-9"
    assert content.content_type == "section"
    assert content.content_order == 4
    assert content.source_metadata == {"slug": "access"}
    assert document.stage2_job_id is None


def test_published_document_does_not_fabricate_missing_content_id():
    payload = {"content": [{"type": "paragraph", "text": "Unidentified content."}]}
    document = PublishedDocumentAdapter().parse(json.dumps(payload).encode(), context())
    assert document.contents[0].content_id is None


def test_published_document_reads_nested_editor_text_and_id():
    payload = {
        "document": {
            "document_id": "64",
            "version_number": 2,
            "content": [
                {
                    "type": "paragraph",
                    "attrs": {"id": "node-7"},
                    "content": [{"type": "text", "text": "Nested editor text."}],
                }
            ],
        }
    }
    document = PublishedDocumentAdapter().parse(json.dumps(payload).encode(), context())
    assert document.contents[0].content_id == "node-7"
    assert document.contents[0].text == "Nested editor text."


@pytest.mark.parametrize(
    "payload",
    [
        {"document_id": "65", "content": "text"},
        {"document_version": "3", "content": "text"},
    ],
)
def test_published_document_rejects_identity_mismatch(payload):
    with pytest.raises(ValueError, match="does not match"):
        PublishedDocumentAdapter().parse(json.dumps(payload).encode(), context())


def test_factory_routes_only_published_document_json_to_new_adapter():
    adapter = ArtifactAdapterFactory.for_artifact(
        "documents/64/versions/v2/document.json", "application/json"
    )
    assert isinstance(adapter, PublishedDocumentAdapter)
    legacy = ArtifactAdapterFactory.for_artifact(
        "documents/64/Sectioning/ocr/sections.json", "application/json"
    )
    assert isinstance(legacy, SectionsJsonAdapter)
    with pytest.raises(ValueError, match="Unsupported"):
        ArtifactAdapterFactory.for_artifact("arbitrary.json", "application/json")

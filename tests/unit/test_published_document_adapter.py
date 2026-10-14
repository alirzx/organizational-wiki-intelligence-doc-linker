import json
from pathlib import Path

import pytest

from app.artifacts.base import AdapterContext
from app.artifacts.factory import ArtifactAdapterFactory
from app.artifacts.published_document import PublishedDocumentAdapter
from app.artifacts.sections_json import SectionsJsonAdapter

FIXTURE = Path(__file__).parents[1] / "fixtures" / "published_document.json"


def context(**overrides) -> AdapterContext:
    values = {
        "document_id": "270",
        "organization_id": "org-1",
        "wiki_id": "wiki-main",
        "document_version": "1",
        "title": "Request title",
        "source_filename": "published.pdf",
        "language": "fa",
        "tags": ("policy",),
        "visibility": "restricted",
        "artifact_bucket": "media",
        "artifact_key": "documents/270/versions/v1/document.json",
        "artifact_sha256": "a" * 64,
        "artifact_etag": "etag-1",
        "extra_metadata": {"request_trace": "trace-1"},
    }
    values.update(overrides)
    return AdapterContext(**values)


def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def parse(payload: dict, ctx: AdapterContext | None = None):
    return PublishedDocumentAdapter().parse(
        json.dumps(payload, ensure_ascii=False).encode(), ctx or context()
    )


def test_real_backend_fixture_parses_with_document_identity_and_snapshot_title():
    document = PublishedDocumentAdapter().parse(FIXTURE.read_bytes(), context())

    assert document.document_id == "270"
    assert document.document_version == "1"
    assert document.title == "یک سند تستی"
    assert document.source_kind == "published_snapshot"
    assert document.metadata["backend_snapshot"]["wiki_document_id"] == 58
    assert document.metadata["backend_snapshot"]["source_document_id"] == 270


def test_wiki_document_id_is_metadata_only_and_is_not_compared_to_request_id():
    payload = fixture_payload()
    payload["id"] = 999
    document = parse(payload)
    assert document.document_id == "270"
    assert document.metadata["backend_snapshot"]["wiki_document_id"] == 999


def test_source_document_and_content_source_document_id_must_agree():
    payload = fixture_payload()
    payload["source_document"] = 271
    with pytest.raises(ValueError, match="source_document and"):
        parse(payload, context(document_id="272"))


def test_source_document_must_match_request_document_id():
    payload = fixture_payload()
    payload["source_document"] = 271
    payload["content"]["source_document_id"] = 271
    with pytest.raises(ValueError, match="source_document does not match request"):
        parse(payload)


def test_content_source_document_id_must_match_request_document_id():
    payload = fixture_payload()
    payload["content"]["source_document_id"] = 271
    with pytest.raises(ValueError, match="content.source_document_id"):
        parse(payload)


def test_version_number_must_match_request_version():
    with pytest.raises(ValueError, match="version_number does not match"):
        parse(fixture_payload(), context(document_version="2"))


@pytest.mark.parametrize("title", [None, "", "   "])
def test_request_title_is_fallback_when_snapshot_title_is_absent_or_empty(title):
    payload = fixture_payload()
    if title is None:
        payload.pop("title")
    else:
        payload["title"] = title
    assert parse(payload).title == "Request title"


def test_title_mismatch_is_not_an_identity_error():
    document = parse(fixture_payload(), context(title="Different request title"))
    assert document.title == "یک سند تستی"


def test_pending_publication_status_and_null_published_at_do_not_gate_indexing():
    payload = fixture_payload()
    payload["status"] = "pending_publication"
    payload["published_at"] = None
    document = parse(payload)
    assert document.metadata["backend_snapshot"]["status"] == "pending_publication"
    assert document.metadata["backend_snapshot"]["published_at"] is None


def test_sections_become_ordered_canonical_content_without_fake_ids():
    payload = fixture_payload()
    payload["content"]["sections"] = {
        "جایگاه سوم": "third serialized entry",
        "جایگاه اول": "first-looking name serialized second",
        "جایگاه دوم": "second-looking name serialized third",
    }
    contents = parse(payload).contents

    assert [item.content_title for item in contents] == [
        "جایگاه سوم",
        "جایگاه اول",
        "جایگاه دوم",
    ]
    assert [item.content_order for item in contents] == [1, 2, 3]
    assert all(item.content_type == "section" for item in contents)
    assert all(item.content_id is None for item in contents)
    assert [item.source_metadata for item in contents] == [
        {"section_key": "جایگاه سوم"},
        {"section_key": "جایگاه اول"},
        {"section_key": "جایگاه دوم"},
    ]


def test_empty_cleaned_section_is_skipped_but_serialized_order_is_retained():
    payload = fixture_payload()
    payload["content"]["sections"] = {
        "empty": '<img src="data:image/png;base64,AAAA" />',
        "kept": "Knowledge body",
    }
    contents = parse(payload).contents
    assert len(contents) == 1
    assert contents[0].content_title == "kept"
    assert contents[0].content_order == 2


def test_zero_usable_sections_fails_clearly():
    payload = fixture_payload()
    payload["content"]["sections"] = {"empty": "  \n  "}
    with pytest.raises(ValueError, match="no usable text content"):
        parse(payload)


@pytest.mark.parametrize(
    "sections",
    [
        [],
        "plain text",
        {"section": {"text": "nested shape"}},
        {"section": ["list shape"]},
    ],
)
def test_invalid_section_shapes_are_rejected_without_coercion(sections):
    payload = fixture_payload()
    payload["content"]["sections"] = sections
    with pytest.raises(ValueError, match="schema is invalid"):
        parse(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source_document",), "270"),
        (("version_number",), "1"),
        (("content", "source_document_id"), "270"),
        (("version_number",), 0),
    ],
)
def test_identity_and_version_fields_are_strict_positive_integers(path, value):
    payload = fixture_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="schema is invalid"):
        parse(payload)


def test_backend_and_request_metadata_are_namespaced_and_acl_stays_authoritative():
    document = parse(fixture_payload())
    backend = document.metadata["backend_snapshot"]

    assert backend == {
        "wiki_document_id": 58,
        "source_document_id": 270,
        "position": "جایگاه اول",
        "owners": [1, 2],
        "status": "published",
        "version_number": 1,
        "source_document": 270,
        "source_version": None,
        "published_at": "2026-10-14T00:00:00Z",
        "available_actions": {
            "edit": True,
            "start_edit": False,
            "submit_for_publication": False,
        },
        "hyperlink_rebuild": {
            "error_message": "",
            "finished_at": None,
            "source_content_hash": "",
            "started_at": None,
            "status": "not_started",
        },
        "created_at": "2026-10-05T12:10:45.703546Z",
        "updated_at": "2026-10-14T00:00:00Z",
    }
    assert document.metadata["request_extra"] == {"request_trace": "trace-1"}
    assert "owners" not in document.source_metadata


def test_harmless_future_metadata_is_preserved_separately():
    payload = fixture_payload()
    payload["future_metadata"] = {"z": 1, "a": 2}
    payload["content"]["future_content_metadata"] = "value"
    backend = parse(payload).metadata["backend_snapshot"]
    assert backend["extra"] == {"future_metadata": {"a": 2, "z": 1}}
    assert backend["content_extra"] == {"future_content_metadata": "value"}


@pytest.mark.parametrize(
    "inline_image",
    [
        '<img alt="x" src="data:image/png;base64,AAAA" />',
        "![alt](data:image/jpeg;base64,BBBB)",
        "data:image/webp;base64,CCCC",
    ],
)
def test_inline_data_images_are_removed_from_canonical_text(inline_image):
    payload = fixture_payload()
    payload["content"]["sections"] = {
        "images": f"before {inline_image} after http://dx.doi.org/10.1/example"
    }
    text = parse(payload).contents[0].text
    lowered = text.casefold()
    assert "before" in text
    assert "after" in text
    assert "http://dx.doi.org/10.1/example" in text
    assert "data:image" not in lowered
    assert ";base64," not in lowered
    assert "<img" not in lowered


def test_schema_error_does_not_echo_raw_inline_image_payload():
    payload = fixture_payload()
    payload["content"]["sections"] = {"bad": {"raw": "data:image/png;base64,SECRET"}}
    with pytest.raises(ValueError) as raised:
        parse(payload)
    assert "SECRET" not in str(raised.value)


def test_factory_routes_only_versioned_published_document_json_to_new_adapter():
    adapter = ArtifactAdapterFactory.for_artifact(
        "documents/270/versions/v1/document.json", "application/json"
    )
    assert isinstance(adapter, PublishedDocumentAdapter)
    legacy = ArtifactAdapterFactory.for_artifact(
        "documents/270/Sectioning/ocr/sections.json", "application/json"
    )
    assert isinstance(legacy, SectionsJsonAdapter)
    with pytest.raises(ValueError, match="Unsupported"):
        ArtifactAdapterFactory.for_artifact("arbitrary.json", "application/json")

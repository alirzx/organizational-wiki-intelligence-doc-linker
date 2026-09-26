import pytest
from pydantic import ValidationError

from app.api.schemas import IndexReadyRequest


def base():
    return {
        "document_id": "59", "organization_id": "org-1", "wiki_id": "wiki-main", "document_version": "1",
        "artifact": {"bucket": "media", "object_key": "documents/59/Sectioning/ocr/sections.json"},
        "document": {"title": "Doc"},
    }


def test_index_request_accepts_stage2_path():
    assert IndexReadyRequest.model_validate(base()).document_id == "59"


def test_index_request_rejects_wrong_document_path():
    data = base()
    data["artifact"]["object_key"] = "documents/60/Sectioning/ocr/sections.json"
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


def test_index_request_rejects_path_traversal():
    data = base()
    data["artifact"]["object_key"] = "documents/59/Sectioning/../secret.json"
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


def test_index_request_rejects_non_section_json_artifact():
    data = base()
    data["artifact"]["object_key"] = "documents/59/Sectioning/ocr/processing-summary.json"
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


def test_manifest_must_be_beside_artifact():
    data = base()
    data["manifest"] = {
        "bucket": "media",
        "object_key": "documents/59/Sectioning/main/manifest.json",
    }
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)

import pytest
from pydantic import ValidationError

from app.api.schemas import IndexReadyRequest


def base():
    return {
        "document_id": "59",
        "organization_id": "org-1",
        "wiki_id": "wiki-main",
        "document_version": "2",
        "artifact": {
            "bucket": "media",
            "object_key": "documents/59/versions/v2/document.json",
            "uri": "s3://media/documents/59/versions/v2/document.json",
        },
        "document": {"title": "Doc"},
    }


def test_index_request_accepts_published_snapshot_path():
    request = IndexReadyRequest.model_validate(base())
    assert request.document_id == "59"
    assert request.document_version == "2"


def test_index_request_requires_document_version():
    data = base()
    del data["document_version"]
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


@pytest.mark.parametrize(
    "object_key",
    [
        "documents/60/versions/v2/document.json",
        "documents/59/versions/v3/document.json",
        "documents/59/versions/v2/sections.json",
        "documents/59/Sectioning/ocr/sections.json",
    ],
)
def test_index_request_rejects_noncanonical_published_path(object_key):
    data = base()
    data["artifact"]["object_key"] = object_key
    data["artifact"].pop("uri")
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


def test_index_request_rejects_path_traversal():
    data = base()
    data["artifact"]["object_key"] = "documents/59/versions/v2/../document.json"
    data["artifact"].pop("uri")
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


@pytest.mark.parametrize("version", ["0", "-1", "v2", "latest", "2.0", "02"])
def test_index_request_rejects_nonpositive_or_nonnumeric_versions(version):
    data = base()
    data["document_version"] = version
    data["artifact"]["object_key"] = f"documents/59/versions/v{version}/document.json"
    data["artifact"].pop("uri")
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


def test_index_request_rejects_uri_mismatch():
    data = base()
    data["artifact"]["uri"] = "s3://media/documents/59/versions/v1/document.json"
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)


def test_index_request_rejects_legacy_manifest_field():
    data = base()
    data["manifest"] = {
        "bucket": "media",
        "object_key": "documents/59/Sectioning/manifest.json",
    }
    with pytest.raises(ValidationError):
        IndexReadyRequest.model_validate(data)

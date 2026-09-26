import json

from app.artifacts.base import AdapterContext
from app.artifacts.sections_json import SectionsJsonAdapter


def ctx():
    return AdapterContext(
        document_id="59", organization_id="org-1", wiki_id="wiki-main", document_version="1",
        title="Doc", source_filename="a.pdf", language="fa", tags=("policy",), visibility="internal",
        acl_groups=("sec",), artifact_bucket="media",
        artifact_key="documents/59/Sectioning/ocr/sections.json", artifact_sha256="a" * 64, artifact_etag="e",
    )


def test_sections_json_preserves_provenance():
    payload = {
        "document-id": "59", "job-id": "stage2-job", "sections": [{
            "section-id": "section-003", "type-id": 6, "title": "فرایند، رویه یا مراحل",
            "text": "مرحله اول.\n\nمرحله دوم.", "order": 3, "page-start": 5, "page-end": 8,
            "source-blocks": [41, 42], "confidence": 0.9, "classifier": "mixed",
        }],
    }
    doc = SectionsJsonAdapter().parse(json.dumps(payload, ensure_ascii=False).encode(), ctx())
    section = doc.sections[0]
    assert doc.stage2_job_id == "stage2-job"
    assert section.section_id == "section-003"
    assert section.page_start == 5 and section.page_end == 8
    assert section.source_blocks == (41, 42)

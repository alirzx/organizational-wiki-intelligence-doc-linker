from app.artifacts.base import AdapterContext
from app.artifacts.markdown import MarkdownAdapter


def test_markdown_removes_base64_images():
    context = AdapterContext(
        document_id="1", organization_id="o", wiki_id="w", document_version="1", title="D",
        source_filename=None, language="fa", tags=(), visibility="internal", acl_groups=(),
        artifact_bucket="media", artifact_key="documents/1/Sectioning/ocr/sections.md",
        artifact_sha256="b" * 64, artifact_etag=None,
    )
    raw = b'''# \xd9\x87\xd8\xaf\xd9\x81 \xd9\x88 \xd9\x85\xd9\x88\xd8\xb6\xd9\x88\xd8\xb9\n\n\xd9\x85\xd8\xaa\xd9\x86 \xd8\xa7\xd8\xb5\xd9\x84\xdb\x8c\n\n<img src="data:image/png;base64,AAAABBBB" alt="" />\n\n# \xd8\xaf\xd8\xa7\xd9\x85\xd9\x86\xd9\x87\n\n\xd9\x85\xd8\xaa\xd9\x86 \xd8\xaf\xd9\x88\xd9\x85\n'''
    doc = MarkdownAdapter().parse(raw, context)
    assert len(doc.sections) == 2
    assert "base64" not in doc.sections[0].text
    assert doc.sections[0].title == "هدف و موضوع"

from app.chunking.semantic import SemanticChunker
from app.core.config import Settings
from app.models.domain import CanonicalContent, CanonicalDocument
from app.models.embedding import MockEmbeddingService


def build_doc(text, *, content_id="content-001", source_metadata=None):
    return CanonicalDocument(
        document_id="d1", organization_id="o1", wiki_id="w1", document_version="1", title="Doc",
        source_filename="x.pdf", language="fa", tags=(), visibility="internal", acl_groups=(),
        artifact_bucket="media", artifact_key="documents/d1/Sectioning/ocr/sections.json",
        artifact_sha256="d" * 64, artifact_etag=None, source_kind="published_snapshot",
        contents=(CanonicalContent(
            content_id=content_id, content_type="section", content_title="Process",
            text=text, content_order=1, source_metadata=source_metadata or {},
        ),),
    )


def test_chunk_ids_are_deterministic():
    settings = Settings(
        _env_file=None, embedding_backend="mock", qdrant_vector_size=16,
        chunk_target_tokens=60, chunk_max_tokens=100, chunk_min_tokens=10, chunk_overlap_tokens=10,
    )
    chunker = SemanticChunker(settings, MockEmbeddingService(settings))
    doc = build_doc("First sentence with several words for realistic token sizing. " * 80 + "\n\n" + "Second semantic topic with enough repeated content. " * 80)
    a = chunker.chunk(doc, index_revision="job-1")
    b = chunker.chunk(doc, index_revision="job-1")
    assert [x.point_id for x in a] == [x.point_id for x in b]
    assert len(a) > 1
    assert all(x.token_count <= settings.chunk_max_tokens for x in a)
    assert all(x.content_id == "content-001" for x in a)

    next_revision = chunker.chunk(doc, index_revision="job-2")
    assert [x.point_id for x in a] != [x.point_id for x in next_revision]
    assert all(x.index_revision == "job-1" for x in a)


def test_large_published_section_chunks_keep_title_order_and_source_metadata():
    settings = Settings(
        _env_file=None, embedding_backend="mock", qdrant_vector_size=16,
        chunk_target_tokens=60, chunk_max_tokens=100, chunk_min_tokens=10,
        chunk_overlap_tokens=8,
    )
    chunker = SemanticChunker(settings, MockEmbeddingService(settings))
    document = build_doc(
        "Persian and multilingual policy sentence with enough words. " * 100,
        content_id=None,
        source_metadata={"section_key": "Process"},
    )

    chunks = chunker.chunk(document, index_revision="job-published")
    assert len(chunks) > 1
    assert all(item.content_id is None for item in chunks)
    assert all(item.content_title == "Process" for item in chunks)
    assert all(item.content_order == 1 for item in chunks)
    assert all(item.source_metadata == {"section_key": "Process"} for item in chunks)
    assert all(item.token_count <= settings.chunk_max_tokens for item in chunks)

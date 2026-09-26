from app.chunking.semantic import SemanticChunker
from app.core.config import Settings
from app.models.domain import CanonicalDocument, CanonicalSection
from app.models.embedding import MockEmbeddingService


def build_doc(text):
    return CanonicalDocument(
        document_id="d1", organization_id="o1", wiki_id="w1", document_version="1", title="Doc",
        source_filename="x.pdf", language="fa", tags=(), visibility="internal", acl_groups=(),
        artifact_bucket="media", artifact_key="documents/d1/Sectioning/ocr/sections.json",
        artifact_sha256="d" * 64, artifact_etag=None, stage2_job_id=None, stage2_pipeline_version=None,
        sections=(CanonicalSection("section-001", 6, "Process", text, 1, 1, 2, (1, 2)),),
    )


def test_chunk_ids_are_deterministic():
    settings = Settings(
        _env_file=None, embedding_backend="mock", qdrant_vector_size=16,
        chunk_target_tokens=60, chunk_max_tokens=100, chunk_min_tokens=10, chunk_overlap_tokens=10,
    )
    chunker = SemanticChunker(settings, MockEmbeddingService(settings))
    doc = build_doc("First sentence with several words for realistic token sizing. " * 80 + "\n\n" + "Second semantic topic with enough repeated content. " * 80)
    a = chunker.chunk(doc)
    b = chunker.chunk(doc)
    assert [x.point_id for x in a] == [x.point_id for x in b]
    assert len(a) > 1
    assert all(x.token_count <= settings.chunk_max_tokens for x in a)
    assert all(x.section_id == "section-001" for x in a)

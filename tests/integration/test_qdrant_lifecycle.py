import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient

from app.core.config import Settings
from app.models.domain import ChunkRecord, EmbeddedChunk
from app.vector.qdrant_store import QdrantStore


def _chunk(point_id: str, version: str, chunk_index: int, text: str) -> EmbeddedChunk:
    record = ChunkRecord(
        point_id=point_id,
        document_id="doc-1",
        organization_id="org-1",
        wiki_id="wiki-1",
        document_version=version,
        document_title="Test document",
        source_filename="source.pdf",
        language="fa",
        tags=("policy",),
        visibility="internal",
        acl_groups=("security",),
        artifact_bucket="media",
        artifact_key="documents/doc-1/Sectioning/ocr/sections.json",
        artifact_sha256="a" * 64,
        artifact_etag="etag-1",
        stage2_job_id="stage2-job",
        stage2_pipeline_version="7.0.0",
        section_id="section-001",
        section_type_id=6,
        section_title="Process",
        section_order=1,
        page_start=1,
        page_end=2,
        source_blocks=(1, 2),
        section_confidence=0.91,
        section_classifier="hybrid",
        chunk_index=chunk_index,
        text=text,
        content_hash=("b" if version == "1" else "c") * 64,
        token_count=10,
    )
    return EmbeddedChunk(
        chunk=record,
        dense=[1.0, 0.0, 0.0, 0.0],
        sparse_indices=[1, 4],
        sparse_values=[0.9, 0.3],
    )


def test_qdrant_document_lifecycle_in_memory():
    settings = Settings(
        _env_file=None,
        qdrant_vector_size=4,
        qdrant_collection="test_wiki_chunks",
        enable_sparse=True,
    )
    store = QdrantStore(settings, client=QdrantClient(location=":memory:"))
    store.ensure_collection()

    v1 = [_chunk("11111111-1111-4111-8111-111111111111", "1", 0, "alpha")]
    store.upsert(v1, embedding_model="test", chunker_version="test", indexed_at="2026-09-26T00:00:00Z")
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 1

    v2 = [_chunk("22222222-2222-4222-8222-222222222222", "2", 0, "beta")]
    store.upsert(v2, embedding_model="test", chunker_version="test", indexed_at="2026-09-26T00:01:00Z")
    assert store.count_document("org-1", "wiki-1", "doc-1") == 2

    deleted_old = store.delete_stale_versions("org-1", "wiki-1", "doc-1", "2")
    assert deleted_old == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 0
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1

    removed = store.delete_document("org-1", "wiki-1", "doc-1")
    assert removed == 1
    assert store.count_document("org-1", "wiki-1", "doc-1") == 0

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient

from app.api.schemas import SearchFilters
from app.core.config import Settings
from app.models.domain import ChunkRecord, EmbeddedChunk
from app.vector.qdrant_store import QdrantStore

POINT_IDS = {
    "1": "11111111-1111-4111-8111-111111111111",
    "2": "22222222-2222-4222-8222-222222222222",
    "3": "33333333-3333-4333-8333-333333333333",
    "3b": "44444444-4444-4444-8444-444444444444",
}


def _chunk(version: str, text: str, *, point_key: str | None = None) -> EmbeddedChunk:
    record = ChunkRecord(
        point_id=POINT_IDS[point_key or version],
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
        artifact_key=f"documents/doc-1/versions/v{version}/document.json",
        artifact_sha256=("a" if version == "1" else "b") * 64,
        artifact_etag=f"etag-{version}",
        source_kind="published_snapshot",
        content_id="backend-content-1",
        content_type="section",
        content_title="Process",
        content_order=1,
        source_metadata={},
        stage2_job_id=None,
        stage2_pipeline_version=None,
        section_type_id=None,
        page_start=None,
        page_end=None,
        source_blocks=(),
        section_confidence=None,
        section_classifier=None,
        chunk_index=0,
        text=text,
        content_hash=("c" if version == "1" else "d") * 64,
        token_count=10,
    )
    return EmbeddedChunk(
        chunk=record,
        dense=[1.0, 0.0, 0.0, 0.0],
        sparse_indices=[1, 4],
        sparse_values=[0.9, 0.3],
    )


def _store() -> QdrantStore:
    settings = Settings(
        _env_file=None,
        qdrant_vector_size=4,
        qdrant_collection="test_wiki_chunks",
        enable_sparse=True,
    )
    store = QdrantStore(settings, client=QdrantClient(location=":memory:"))
    store.ensure_collection()
    return store


def _upsert(store: QdrantStore, chunks: list[EmbeddedChunk]) -> None:
    store.upsert(
        chunks,
        embedding_model="test",
        chunker_version="test",
        indexed_at="2026-09-26T00:00:00Z",
        version_state="inactive",
    )


def _search_texts(store: QdrantStore) -> list[str]:
    points = store.search(
        dense=[1.0, 0.0, 0.0, 0.0],
        sparse_indices=[],
        sparse_values=[],
        organization_id="org-1",
        wiki_id="wiki-1",
        filters=SearchFilters(),
        limit=20,
        mode="dense",
    )
    return [str(point.payload["text"]) for point in points]


def test_published_version_lifecycle_preserves_history_and_searches_only_active():
    store = _store()

    _upsert(store, [_chunk("1", "version one")])
    assert _search_texts(store) == []
    first = store.activate_version("org-1", "wiki-1", "doc-1", "1", replace_existing=True)
    assert first.activated
    assert _search_texts(store) == ["version one"]

    _upsert(store, [_chunk("2", "version two")])
    assert _search_texts(store) == ["version one"]
    second = store.activate_version("org-1", "wiki-1", "doc-1", "2", replace_existing=True)
    assert second.activated
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1
    assert _search_texts(store) == ["version two"]

    # Failed v3 is preserved inactive; v2 stays searchable.
    _upsert(store, [_chunk("3", "failed version three")])
    assert store.count_document("org-1", "wiki-1", "doc-1", "3") == 1
    assert _search_texts(store) == ["version two"]

    third = store.activate_version("org-1", "wiki-1", "doc-1", "3", replace_existing=True)
    assert third.activated
    assert _search_texts(store) == ["failed version three"]
    assert store.count_document("org-1", "wiki-1", "doc-1") == 3

    # A delayed older completion remains historical and cannot downgrade v3.
    _upsert(store, [_chunk("2", "late version two")])
    late = store.activate_version("org-1", "wiki-1", "doc-1", "2", replace_existing=True)
    assert not late.activated
    assert late.active_version == "3"
    assert late.reason == "newer-version-already-active"
    assert _search_texts(store) == ["failed version three"]


def test_same_version_cleanup_only_removes_obsolete_points_from_that_version():
    store = _store()
    _upsert(store, [_chunk("1", "one"), _chunk("2", "two")])
    _upsert(store, [_chunk("3", "three"), _chunk("3", "obsolete", point_key="3b")])
    removed = store.delete_obsolete_points(
        "org-1", "wiki-1", "doc-1", "3", [POINT_IDS["3"]]
    )
    assert removed == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "3") == 1


def test_activation_update_failure_restores_previous_active_version(monkeypatch):
    store = _store()
    _upsert(store, [_chunk("1", "one")])
    store.activate_version("org-1", "wiki-1", "doc-1", "1", replace_existing=True)
    _upsert(store, [_chunk("2", "two")])

    original = store.client.batch_update_points
    calls = 0

    def partially_failing_batch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_operation = kwargs["update_operations"][:1]
            original(*args, **{**kwargs, "update_operations": first_operation})
            raise RuntimeError("simulated activation failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store.client, "batch_update_points", partially_failing_batch)
    with pytest.raises(RuntimeError, match="activation failure"):
        store.activate_version("org-1", "wiki-1", "doc-1", "2", replace_existing=True)
    assert store.get_active_version("org-1", "wiki-1", "doc-1") == "1"
    assert _search_texts(store) == ["one"]


def test_replacement_disabled_preserves_current_active_version():
    store = _store()
    _upsert(store, [_chunk("1", "one")])
    store.activate_version("org-1", "wiki-1", "doc-1", "1", replace_existing=True)
    _upsert(store, [_chunk("2", "two")])
    result = store.activate_version(
        "org-1", "wiki-1", "doc-1", "2", replace_existing=False
    )
    assert not result.activated
    assert result.reason == "replacement-disabled"
    assert store.get_active_version("org-1", "wiki-1", "doc-1") == "1"
    assert _search_texts(store) == ["one"]


def test_explicit_document_delete_remains_independent():
    store = _store()
    _upsert(store, [_chunk("1", "one"), _chunk("2", "two")])
    assert store.delete_document("org-1", "wiki-1", "doc-1", "1") == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 0
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient

from app.api.schemas import SearchFilters
from app.core.config import Settings
from app.core.ids import stable_point_id
from app.models.domain import ChunkRecord, EmbeddedChunk
from app.vector.qdrant_store import ActiveStateInvariantError, QdrantStore


def _chunk(
    version: str,
    text: str,
    *,
    revision: str,
    point_key: str | None = None,
    source_kind: str = "published_snapshot",
) -> EmbeddedChunk:
    record = ChunkRecord(
        point_id=stable_point_id(version, revision, point_key or text),
        document_id="doc-1",
        organization_id="org-1",
        wiki_id="wiki-1",
        document_version=version,
        index_revision=revision,
        document_title="Test document",
        source_filename="source.pdf",
        language="fa",
        tags=("policy",),
        visibility="internal",
        artifact_bucket="media",
        artifact_key=f"documents/doc-1/versions/v{version}/document.json",
        artifact_sha256=("a" if version == "1" else "b") * 64,
        artifact_etag=f"etag-{version}",
        source_kind=source_kind,
        content_id="backend-content-1",
        content_type="section",
        content_title="Process",
        content_order=1,
        source_metadata={},
        stage2_job_id="stage2-job" if source_kind == "legacy_stage2" else None,
        stage2_pipeline_version="2.0" if source_kind == "legacy_stage2" else None,
        section_type_id=4 if source_kind == "legacy_stage2" else None,
        page_start=1 if source_kind == "legacy_stage2" else None,
        page_end=2 if source_kind == "legacy_stage2" else None,
        source_blocks=(3,) if source_kind == "legacy_stage2" else (),
        section_confidence=0.9 if source_kind == "legacy_stage2" else None,
        section_classifier="legacy" if source_kind == "legacy_stage2" else None,
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


def _upsert(
    store: QdrantStore,
    chunks: list[EmbeddedChunk],
    *,
    state: str = "inactive",
) -> None:
    store.upsert(
        chunks,
        embedding_model="test",
        chunker_version="test",
        indexed_at="2026-09-26T00:00:00Z",
        version_state=state,
    )


def _activate(store: QdrantStore, chunk: EmbeddedChunk, *, replace: bool = True):
    record = chunk.chunk
    return store.activate_version(
        record.organization_id,
        record.wiki_id,
        record.document_id,
        record.document_version,
        record.index_revision,
        replace_existing=replace,
        expected_point_count=store.count_revision(
            record.organization_id,
            record.wiki_id,
            record.document_id,
            record.document_version,
            record.index_revision,
        ),
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


def test_published_version_lifecycle_preserves_history_and_blocks_late_version():
    store = _store()
    v1 = _chunk("1", "version one", revision="rev-v1")
    v2 = _chunk("2", "version two", revision="rev-v2")
    v3 = _chunk("3", "version three", revision="rev-v3")

    _upsert(store, [v1])
    assert _activate(store, v1).activated
    _upsert(store, [v2])
    assert _search_texts(store) == ["version one"]
    assert _activate(store, v2).activated
    _upsert(store, [v3])
    assert _activate(store, v3).activated

    assert store.count_document("org-1", "wiki-1", "doc-1") == 3
    assert _search_texts(store) == ["version three"]

    late_v2 = _chunk("2", "late version two", revision="rev-v2-late")
    _upsert(store, [late_v2])
    late = _activate(store, late_v2)
    assert not late.activated
    assert late.active_version == "3"
    assert late.active_index_revision == "rev-v3"
    assert late.reason == "newer-version-already-active"
    assert _search_texts(store) == ["version three"]


def test_same_version_shadow_candidate_failure_and_success_preserve_history():
    store = _store()
    v1 = _chunk("1", "version one", revision="rev-v1")
    v2 = _chunk("2", "version two", revision="rev-v2")
    rev_a = _chunk("3", "revision A", revision="rev-A")
    for chunk in (v1, v2, rev_a):
        _upsert(store, [chunk])
        _activate(store, chunk)
    assert _search_texts(store) == ["revision A"]

    rev_b = _chunk("3", "revision B failed", revision="rev-B")
    _upsert(store, [rev_b])
    assert _search_texts(store) == ["revision A"]
    with pytest.raises(ActiveStateInvariantError, match="Candidate point count"):
        store.activate_version(
            "org-1", "wiki-1", "doc-1", "3", "rev-B",
            replace_existing=True, expected_point_count=2,
        )
    assert _search_texts(store) == ["revision A"]

    rev_c = _chunk("3", "revision C", revision="rev-C")
    _upsert(store, [rev_c])
    switched = _activate(store, rev_c)
    assert switched.activated
    assert switched.active_index_revision == "rev-C"
    assert _search_texts(store) == ["revision C"]
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1
    assert store.count_revision("org-1", "wiki-1", "doc-1", "3", "rev-A") == 1


def test_cleanup_only_removes_obsolete_points_from_candidate_revision():
    store = _store()
    active = _chunk("3", "active", revision="rev-A")
    keep = _chunk("3", "keep", revision="rev-B", point_key="keep")
    obsolete = _chunk("3", "obsolete", revision="rev-B", point_key="obsolete")
    _upsert(store, [active])
    _activate(store, active)
    _upsert(store, [keep, obsolete])

    removed = store.delete_obsolete_points(
        "org-1", "wiki-1", "doc-1", "3", "rev-B", [keep.chunk.point_id]
    )
    assert removed == 1
    assert store.count_revision("org-1", "wiki-1", "doc-1", "3", "rev-A") == 1
    assert _search_texts(store) == ["active"]


def test_activation_operation_failure_restores_previous_active_revision(monkeypatch):
    store = _store()
    previous = _chunk("1", "one", revision="rev-A")
    candidate = _chunk("2", "two", revision="rev-B")
    _upsert(store, [previous])
    _activate(store, previous)
    _upsert(store, [candidate])

    original = store.client.batch_update_points
    calls = 0

    def partially_failing_batch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            original(*args, **{**kwargs, "update_operations": kwargs["update_operations"][:1]})
            raise RuntimeError("simulated activation failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store.client, "batch_update_points", partially_failing_batch)
    with pytest.raises(RuntimeError, match="activation failure"):
        _activate(store, candidate)
    active = store.get_active_state("org-1", "wiki-1", "doc-1")
    assert active is not None
    assert (active.document_version, active.index_revision) == ("1", "rev-A")
    assert _search_texts(store) == ["one"]


def test_zero_active_partial_failure_is_repaired(monkeypatch):
    store = _store()
    previous = _chunk("1", "one", revision="rev-A")
    candidate = _chunk("2", "two", revision="rev-B")
    _upsert(store, [previous])
    _activate(store, previous)
    _upsert(store, [candidate])

    original = store.client.batch_update_points
    calls = 0

    def deactivate_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            original(*args, **{**kwargs, "update_operations": kwargs["update_operations"][1:]})
            raise RuntimeError("zero active during switch")
        return original(*args, **kwargs)

    monkeypatch.setattr(store.client, "batch_update_points", deactivate_then_fail)
    with pytest.raises(RuntimeError, match="zero active"):
        _activate(store, candidate)
    assert store.get_active_version("org-1", "wiki-1", "doc-1") == "1"
    assert _search_texts(store) == ["one"]


def test_post_activation_invariant_failure_rolls_back(monkeypatch):
    store = _store()
    previous = _chunk("1", "one", revision="rev-A")
    candidate = _chunk("2", "two", revision="rev-B")
    _upsert(store, [previous])
    _activate(store, previous)
    _upsert(store, [candidate])

    original = store.client.batch_update_points
    calls = 0

    def omit_deactivation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(
                *args,
                **{**kwargs, "update_operations": kwargs["update_operations"][:1]},
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(store.client, "batch_update_points", omit_deactivation)
    with pytest.raises(ActiveStateInvariantError, match="Multiple active"):
        _activate(store, candidate)
    assert store.get_active_version("org-1", "wiki-1", "doc-1") == "1"
    assert _search_texts(store) == ["one"]


def test_multiple_active_corruption_is_not_hidden_by_numeric_max():
    store = _store()
    first = _chunk("2", "two", revision="rev-A")
    second = _chunk("3", "three", revision="rev-B")
    _upsert(store, [first, second], state="active")
    with pytest.raises(ActiveStateInvariantError, match="Multiple active"):
        store.get_active_state("org-1", "wiki-1", "doc-1")
    with pytest.raises(ActiveStateInvariantError, match="Multiple active"):
        store.get_active_version("org-1", "wiki-1", "doc-1")


def test_partially_active_revision_is_detected_as_corruption():
    store = _store()
    active = _chunk("3", "active point", revision="rev-A", point_key="active")
    inactive = _chunk(
        "3", "inactive point", revision="rev-A", point_key="inactive"
    )
    _upsert(store, [active], state="active")
    _upsert(store, [inactive], state="inactive")
    with pytest.raises(ActiveStateInvariantError, match="partially active"):
        store.get_active_state("org-1", "wiki-1", "doc-1")


def test_replacement_disabled_preserves_current_active_revision():
    store = _store()
    first = _chunk("1", "one", revision="rev-A")
    second = _chunk("2", "two", revision="rev-B")
    _upsert(store, [first])
    _activate(store, first)
    _upsert(store, [second])
    result = _activate(store, second, replace=False)
    assert not result.activated
    assert result.reason == "replacement-disabled"
    assert result.active_index_revision == "rev-A"
    assert _search_texts(store) == ["one"]


def test_published_payload_omits_fake_legacy_section_fields():
    published = _chunk("1", "published", revision="rev-A")
    payload = published.chunk.payload(
        embedding_model="test",
        chunker_version="test",
        indexed_at="now",
        version_state="inactive",
    )
    assert payload["content_id"] == "backend-content-1"
    assert "section_id" not in payload
    assert "section_title" not in payload
    assert "section_order" not in payload

    legacy = _chunk(
        "1", "legacy", revision="legacy-v1", source_kind="legacy_stage2"
    ).chunk.payload(
        embedding_model="test",
        chunker_version="test",
        indexed_at="now",
        version_state="inactive",
    )
    assert legacy["section_id"] == "backend-content-1"
    assert legacy["section_title"] == "Process"
    assert legacy["section_order"] == 1


def test_explicit_document_delete_remains_independent():
    store = _store()
    first = _chunk("1", "one", revision="rev-A")
    second = _chunk("2", "two", revision="rev-B")
    _upsert(store, [first, second])
    assert store.delete_document("org-1", "wiki-1", "doc-1", "1") == 1
    assert store.count_document("org-1", "wiki-1", "doc-1", "1") == 0
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1

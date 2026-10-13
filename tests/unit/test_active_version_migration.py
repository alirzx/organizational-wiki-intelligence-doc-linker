import pytest
from qdrant_client import QdrantClient, models

from app.core.config import Settings
from app.vector.qdrant_store import QdrantStore
from scripts.migrate_active_versions import migrate


def _store(*, create=True):
    settings = Settings(
        _env_file=None,
        qdrant_collection="migration_test",
        qdrant_vector_size=4,
        enable_sparse=False,
    )
    store = QdrantStore(settings, client=QdrantClient(location=":memory:"))
    if create:
        store.ensure_collection()
    return store


def _put(
    store,
    point_id,
    version,
    *,
    revision=None,
    state=None,
    document_id="doc-1",
):
    payload = {
        "organization_id": "org-1",
        "wiki_id": "wiki-1",
        "document_id": document_id,
        "document_version": version,
    }
    if revision is not None:
        payload["index_revision"] = revision
    if state is not None:
        payload["version_state"] = state
    store.client.upsert(
        collection_name=store.settings.qdrant_collection,
        points=[models.PointStruct(
            id=point_id,
            vector={store.settings.qdrant_dense_vector_name: [1.0, 0.0, 0.0, 0.0]},
            payload=payload,
        )],
        wait=True,
    )


def _payloads(store):
    points, _ = store.client.scroll(
        collection_name=store.settings.qdrant_collection,
        limit=100,
        with_payload=True,
        with_vectors=False,
    )
    return {point.id: dict(point.payload or {}) for point in points}


def test_single_legacy_version_is_safe_and_receives_deterministic_revision():
    store = _store()
    _put(store, 1, "2")
    _put(store, 2, "2")

    before = _payloads(store)
    dry_run = migrate(apply=False, store=store)
    assert dry_run[0]["status"] == "safe-single-version"
    assert dry_run[0]["proposed_active_version"] == "2"
    assert not dry_run[0]["applied"]
    assert _payloads(store) == before

    applied = migrate(apply=True, store=store)
    assert applied[0]["applied"]
    active = store.get_active_state("org-1", "wiki-1", "doc-1")
    assert active is not None
    assert (active.document_version, active.index_revision, active.point_count) == (
        "2", "legacy-v2", 2,
    )
    assert {
        payload["index_revision"] for payload in _payloads(store).values()
    } == {"legacy-v2"}


def test_multiple_versions_without_mapping_are_ambiguous_and_never_written():
    store = _store()
    _put(store, 1, "1")
    _put(store, 2, "2")
    before = _payloads(store)

    report = migrate(apply=True, store=store)
    assert report[0]["status"] == "ambiguous-multiple-versions"
    assert report[0]["requires_explicit_active_version"]
    assert not report[0]["applied"]
    assert _payloads(store) == before


def test_explicit_mapping_activates_requested_version_and_preserves_history():
    store = _store()
    _put(store, 1, "1")
    _put(store, 2, "2")

    report = migrate(
        apply=True,
        active_map={("org-1", "wiki-1", "doc-1"): "1"},
        store=store,
    )
    assert report[0]["status"] == "explicit-map"
    active = store.get_active_state("org-1", "wiki-1", "doc-1")
    assert active is not None
    assert (active.document_version, active.index_revision) == ("1", "legacy-v1")
    assert store.count_document("org-1", "wiki-1", "doc-1", "2") == 1
    assert {
        payload["index_revision"] for payload in _payloads(store).values()
    } == {"legacy-v1", "legacy-v2"}


def test_mapping_to_missing_version_fails_before_writes():
    store = _store()
    _put(store, 1, "1")
    before = _payloads(store)
    with pytest.raises(ValueError, match="does not exist"):
        migrate(
            apply=True,
            active_map={("org-1", "wiki-1", "doc-1"): "2"},
            store=store,
        )
    assert _payloads(store) == before


@pytest.mark.parametrize("apply", [False, True])
def test_missing_collection_fails_in_dry_run_and_apply(apply):
    with pytest.raises(RuntimeError, match="does not exist"):
        migrate(apply=apply, store=_store(create=False))


def test_existing_valid_active_state_is_preserved():
    store = _store()
    _put(store, 1, "1", revision="existing-rev", state="active")
    _put(store, 2, "2", revision="other-rev", state="inactive")

    report = migrate(apply=True, store=store)
    assert report[0]["status"] == "already-valid"
    active = store.get_active_state("org-1", "wiki-1", "doc-1")
    assert active is not None
    assert (active.document_version, active.index_revision) == ("1", "existing-rev")


def test_multiple_active_state_is_reported_as_corruption_without_writes():
    store = _store()
    _put(store, 1, "2", revision="rev-A", state="active")
    _put(store, 2, "3", revision="rev-B", state="active")
    before = _payloads(store)

    report = migrate(apply=True, store=store)
    assert report[0]["status"] == "multiple-active-corruption"
    assert not report[0]["applied"]
    assert _payloads(store) == before

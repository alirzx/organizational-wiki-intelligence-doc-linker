from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qdrant_client import models

from app.core.config import get_settings
from app.vector.qdrant_store import QdrantStore

DocumentKey = tuple[str, str, str]
ActiveMap = dict[DocumentKey, str]


def _positive_version(value: Any) -> int | None:
    text = str(value or "")
    if not text.isascii() or not text.isdigit() or int(text) <= 0:
        return None
    return int(text)


def _legacy_revision(document_version: str) -> str:
    return f"legacy-v{document_version}"


@dataclass(frozen=True)
class LegacyPoint:
    point_id: Any
    document_version: str
    index_revision: str
    has_index_revision: bool
    version_state: str | None


@dataclass
class DocumentInventory:
    points: list[LegacyPoint] = field(default_factory=list)
    invalid_reasons: list[str] = field(default_factory=list)

    @property
    def versions(self) -> list[str]:
        return sorted(
            {point.document_version for point in self.points},
            key=lambda value: _positive_version(value) or 0,
        )

    def revisions(self, document_version: str) -> set[str]:
        return {
            point.index_revision
            for point in self.points
            if point.document_version == document_version
        }

    def revision_count(self, document_version: str, index_revision: str) -> int:
        return sum(
            point.document_version == document_version
            and point.index_revision == index_revision
            for point in self.points
        )

    def active_counts(self) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for point in self.points:
            if point.version_state == "active":
                counts[(point.document_version, point.index_revision)] += 1
        return dict(counts)


def discover_documents(store: QdrantStore) -> dict[DocumentKey, DocumentInventory]:
    discovered: dict[DocumentKey, DocumentInventory] = defaultdict(DocumentInventory)
    offset = None
    while True:
        points, offset = store.client.scroll(
            collection_name=store.settings.qdrant_collection,
            limit=256,
            offset=offset,
            with_payload=[
                "organization_id",
                "wiki_id",
                "document_id",
                "document_version",
                "index_revision",
                "version_state",
            ],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            identity = (
                str(payload.get("organization_id") or ""),
                str(payload.get("wiki_id") or ""),
                str(payload.get("document_id") or ""),
            )
            if not all(identity):
                continue
            inventory = discovered[identity]
            version = str(payload.get("document_version") or "")
            if _positive_version(version) is None:
                inventory.invalid_reasons.append(
                    f"point {point.id} has invalid document_version {version!r}"
                )
                continue
            raw_revision = str(payload.get("index_revision") or "")
            inventory.points.append(LegacyPoint(
                point_id=point.id,
                document_version=version,
                index_revision=raw_revision or _legacy_revision(version),
                has_index_revision=bool(raw_revision),
                version_state=(
                    str(payload["version_state"])
                    if payload.get("version_state") is not None
                    else None
                ),
            ))
        if offset is None:
            break
    return dict(discovered)


def load_active_map(path: str | Path) -> ActiveMap:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read active version map: {exc}") from exc
    if not isinstance(raw, list):
        raise TypeError("Active version map must be a JSON array")
    active_map: ActiveMap = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(f"Active version map entry {index} must be an object")
        required = {
            "organization_id", "wiki_id", "document_id", "document_version"
        }
        if set(entry) != required:
            raise ValueError(
                f"Active version map entry {index} must contain exactly {sorted(required)}"
            )
        key = tuple(str(entry[field]) for field in (
            "organization_id", "wiki_id", "document_id"
        ))
        version = str(entry["document_version"])
        if not all(key) or _positive_version(version) is None:
            raise ValueError(f"Active version map entry {index} has invalid identity/version")
        if key in active_map:
            raise ValueError(f"Duplicate active version map entry for {key}")
        active_map[key] = version
    return active_map


def _validate_active_map(
    inventories: dict[DocumentKey, DocumentInventory], active_map: ActiveMap
) -> None:
    for key, version in active_map.items():
        inventory = inventories.get(key)
        if inventory is None:
            raise ValueError(f"Mapped document does not exist in Qdrant: {key}")
        if version not in inventory.versions:
            raise ValueError(f"Mapped version v{version} does not exist for document {key}")


def _plan_document(
    key: DocumentKey,
    inventory: DocumentInventory,
    mapped_version: str | None,
) -> dict[str, Any]:
    organization_id, wiki_id, document_id = key
    active_counts = inventory.active_counts()
    active_states = [
        {
            "document_version": version,
            "index_revision": revision,
            "active_point_count": count,
        }
        for (version, revision), count in sorted(active_counts.items())
    ]
    row: dict[str, Any] = {
        "organization_id": organization_id,
        "wiki_id": wiki_id,
        "document_id": document_id,
        "versions": inventory.versions,
        "current_active_state": active_states[0] if len(active_states) == 1 else active_states,
        "proposed_active_version": None,
        "proposed_index_revision": None,
        "status": "invalid",
        "reason": "",
        "requires_explicit_active_version": False,
        "applied": False,
    }
    if inventory.invalid_reasons:
        row["reason"] = "; ".join(inventory.invalid_reasons)
        return row
    if not inventory.points:
        row["reason"] = "document has no valid versioned points"
        return row
    if len(active_counts) > 1:
        row["status"] = "multiple-active-corruption"
        row["reason"] = "more than one active (document_version, index_revision) exists"
        return row
    if len(active_counts) == 1:
        (version, revision), active_count = next(iter(active_counts.items()))
        total_count = inventory.revision_count(version, revision)
        if active_count != total_count:
            row["reason"] = (
                "active revision is only partially active: "
                f"{active_count} of {total_count} points"
            )
            return row
        if mapped_version is not None and mapped_version != version:
            row["reason"] = (
                f"explicit map v{mapped_version} conflicts with valid active v{version}"
            )
            return row
        row.update({
            "proposed_active_version": version,
            "proposed_index_revision": revision,
            "status": "already-valid",
            "reason": "existing sole active revision is valid and will be preserved",
        })
        return row

    if mapped_version is not None:
        revisions = inventory.revisions(mapped_version)
        if len(revisions) != 1:
            row["reason"] = (
                f"mapped v{mapped_version} has {len(revisions)} internal revisions; "
                "the version-only mapping cannot select one safely"
            )
            return row
        revision = next(iter(revisions))
        row.update({
            "proposed_active_version": mapped_version,
            "proposed_index_revision": revision,
            "status": "explicit-map",
            "reason": "operator supplied the active published version",
        })
        return row

    if len(inventory.versions) == 1:
        version = inventory.versions[0]
        revisions = inventory.revisions(version)
        if len(revisions) != 1:
            row["reason"] = (
                f"single published version has {len(revisions)} internal revisions"
            )
            return row
        revision = next(iter(revisions))
        row.update({
            "proposed_active_version": version,
            "proposed_index_revision": revision,
            "status": "safe-single-version",
            "reason": "exactly one valid published version exists",
        })
        return row

    row.update({
        "status": "ambiguous-multiple-versions",
        "reason": "multiple published versions exist without a trustworthy active state",
        "requires_explicit_active_version": True,
    })
    return row


def _assign_missing_revisions(store: QdrantStore, inventory: DocumentInventory) -> None:
    point_ids_by_revision: dict[str, list[Any]] = defaultdict(list)
    for point in inventory.points:
        if not point.has_index_revision:
            point_ids_by_revision[point.index_revision].append(point.point_id)
    for index_revision, point_ids in point_ids_by_revision.items():
        store.client.set_payload(
            collection_name=store.settings.qdrant_collection,
            payload={"index_revision": index_revision},
            points=point_ids,
            wait=True,
            ordering=models.WriteOrdering.STRONG,
        )


def _apply_plan(
    store: QdrantStore,
    key: DocumentKey,
    inventory: DocumentInventory,
    row: dict[str, Any],
) -> None:
    organization_id, wiki_id, document_id = key
    _assign_missing_revisions(store, inventory)
    version = str(row["proposed_active_version"])
    revision = str(row["proposed_index_revision"])
    expected_count = inventory.revision_count(version, revision)
    if row["status"] == "already-valid":
        active = store.get_active_state(organization_id, wiki_id, document_id)
        if (
            active is None
            or active.document_version != version
            or active.index_revision != revision
            or active.point_count != expected_count
        ):
            raise RuntimeError(
                f"Existing active state changed during migration for document {key}"
            )
        row["applied"] = True
        return

    store.client.set_payload(
        collection_name=store.settings.qdrant_collection,
        payload={"version_state": "inactive"},
        points=store.document_filter(organization_id, wiki_id, document_id),
        wait=True,
        ordering=models.WriteOrdering.STRONG,
    )
    activation = store.activate_version(
        organization_id,
        wiki_id,
        document_id,
        version,
        revision,
        replace_existing=True,
        expected_point_count=expected_count,
    )
    row["activation_reason"] = activation.reason
    row["applied"] = True


def migrate(
    *,
    apply: bool,
    active_map: ActiveMap | None = None,
    store: QdrantStore | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    store = store or QdrantStore(settings)
    collection = store.settings.qdrant_collection
    if not store.client.collection_exists(collection):
        raise RuntimeError(f"Qdrant collection {collection!r} does not exist")

    inventories = discover_documents(store)
    active_map = active_map or {}
    _validate_active_map(inventories, active_map)
    report: list[dict[str, Any]] = []
    for key, inventory in sorted(inventories.items()):
        row = _plan_document(key, inventory, active_map.get(key))
        if apply and row["status"] in {
            "safe-single-version", "already-valid", "explicit-map"
        }:
            _apply_plan(store, key, inventory, row)
        report.append(row)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Conservatively migrate active revision state in an existing Qdrant "
            "collection. The default is a read-only dry run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write index_revision/version_state payloads (omit for dry-run)",
    )
    parser.add_argument(
        "--active-map",
        type=Path,
        help="JSON array mapping ambiguous tenant/wiki/document identities to a version",
    )
    args = parser.parse_args()
    active_map = load_active_map(args.active_map) if args.active_map else None
    print(json.dumps(
        migrate(apply=args.apply, active_map=active_map),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()

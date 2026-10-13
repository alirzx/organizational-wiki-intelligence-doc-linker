from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any

from qdrant_client import models

from app.core.config import get_settings
from app.vector.qdrant_store import QdrantStore

DocumentKey = tuple[str, str, str]


def _positive_version(value: Any) -> int | None:
    text = str(value or "")
    if not text.isascii() or not text.isdigit() or int(text) <= 0:
        return None
    return int(text)


def discover_versions(store: QdrantStore) -> dict[DocumentKey, set[str]]:
    discovered: dict[DocumentKey, set[str]] = defaultdict(set)
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
            version = str(payload.get("document_version") or "")
            if all(identity) and _positive_version(version) is not None:
                discovered[identity].add(version)
        if offset is None:
            break
    return discovered


def migrate(*, apply: bool) -> list[dict[str, Any]]:
    settings = get_settings()
    store = QdrantStore(settings)
    if apply:
        store.ensure_collection()
    elif not store.client.collection_exists(settings.qdrant_collection):
        raise RuntimeError(f"Qdrant collection {settings.qdrant_collection!r} does not exist")
    report: list[dict[str, Any]] = []
    for (organization_id, wiki_id, document_id), versions in sorted(discover_versions(store).items()):
        active_version = max(versions, key=lambda value: _positive_version(value) or 0)
        row: dict[str, Any] = {
            "organization_id": organization_id,
            "wiki_id": wiki_id,
            "document_id": document_id,
            "versions": sorted(versions, key=lambda value: _positive_version(value) or 0),
            "active_version": active_version,
            "applied": apply,
        }
        if apply:
            store.client.set_payload(
                collection_name=settings.qdrant_collection,
                payload={"version_state": "inactive"},
                points=store.document_filter(organization_id, wiki_id, document_id),
                wait=True,
                ordering=models.WriteOrdering.STRONG,
            )
            activation = store.activate_version(
                organization_id,
                wiki_id,
                document_id,
                active_version,
                replace_existing=True,
            )
            row["activation_reason"] = activation.reason
        report.append(row)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mark the latest numeric version of each legacy Qdrant document active. "
            "The default is a read-only dry run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write version_state payloads (omit for dry-run)",
    )
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

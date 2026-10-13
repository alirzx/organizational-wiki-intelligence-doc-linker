from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from app.api.schemas import SearchFilters
from app.core.config import Settings
from app.models.domain import EmbeddedChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedPoint:
    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class ActivationResult:
    activated: bool
    active_version: str | None
    reason: str
    deactivated_points: int = 0


class QdrantStore:
    def __init__(self, settings: Settings, client: QdrantClient | None = None):
        self.settings = settings
        self._ensure_lock = threading.Lock()
        self._ensured = False
        self.client = client or QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=settings.qdrant_timeout_seconds,
            check_compatibility=True,
        )

    def healthcheck(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:  # noqa: BLE001 - health checks must report false, not raise.
            return False

    def ensure_collection(self) -> None:
        if self._ensured:
            return
        with self._ensure_lock:
            if self._ensured:
                return
            name = self.settings.qdrant_collection
            if not self.client.collection_exists(name):
                sparse_config = (
                    {self.settings.qdrant_sparse_vector_name: models.SparseVectorParams()}
                    if self.settings.enable_sparse
                    else {}
                )
                self.client.create_collection(
                    collection_name=name,
                    vectors_config={
                        self.settings.qdrant_dense_vector_name: models.VectorParams(
                            size=self.settings.qdrant_vector_size,
                            distance=models.Distance.COSINE,
                        )
                    },
                    sparse_vectors_config=sparse_config,
                )
            self._ensure_payload_indexes()
            self._ensured = True

    def _ensure_payload_indexes(self) -> None:
        indexed = {
            "organization_id": models.PayloadSchemaType.KEYWORD,
            "wiki_id": models.PayloadSchemaType.KEYWORD,
            "document_id": models.PayloadSchemaType.KEYWORD,
            "document_version": models.PayloadSchemaType.KEYWORD,
            "version_state": models.PayloadSchemaType.KEYWORD,
            "content_id": models.PayloadSchemaType.KEYWORD,
            "content_type": models.PayloadSchemaType.KEYWORD,
            "section_id": models.PayloadSchemaType.KEYWORD,
            "section_type_id": models.PayloadSchemaType.INTEGER,
            "language": models.PayloadSchemaType.KEYWORD,
            "tags": models.PayloadSchemaType.KEYWORD,
            "visibility": models.PayloadSchemaType.KEYWORD,
            "acl_groups": models.PayloadSchemaType.KEYWORD,
            "artifact_sha256": models.PayloadSchemaType.KEYWORD,
        }
        for field_name, field_schema in indexed.items():
            try:
                self.client.create_payload_index(
                    collection_name=self.settings.qdrant_collection,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
            except Exception as exc:
                # Some server versions report an equivalent existing index as an error.
                if "already exists" in str(exc).casefold():
                    continue
                raise RuntimeError(f"Could not create Qdrant payload index {field_name}: {exc}") from exc

    def upsert(
        self,
        chunks: list[EmbeddedChunk],
        *,
        embedding_model: str,
        chunker_version: str,
        indexed_at: str,
        version_state: str = "inactive",
    ) -> None:
        if version_state not in {"active", "inactive"}:
            raise ValueError("version_state must be active or inactive")
        batch_size = self.settings.qdrant_upsert_batch_size
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            points = []
            for item in batch:
                vectors: dict[str, Any] = {
                    self.settings.qdrant_dense_vector_name: item.dense,
                }
                if self.settings.enable_sparse and item.sparse_indices:
                    vectors[self.settings.qdrant_sparse_vector_name] = models.SparseVector(
                        indices=item.sparse_indices, values=item.sparse_values
                    )
                points.append(
                    models.PointStruct(
                        id=item.chunk.point_id,
                        vector=vectors,
                        payload=item.chunk.payload(
                            embedding_model=embedding_model,
                            chunker_version=chunker_version,
                            indexed_at=indexed_at,
                            version_state=version_state,
                        ),
                    )
                )
            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=points,
                wait=True,
            )

    @staticmethod
    def _match(field: str, value: Any) -> models.FieldCondition:
        return models.FieldCondition(key=field, match=models.MatchValue(value=value))

    def document_filter(
        self, organization_id: str, wiki_id: str, document_id: str, document_version: str | None = None
    ) -> models.Filter:
        must = [
            self._match("organization_id", organization_id),
            self._match("wiki_id", wiki_id),
            self._match("document_id", document_id),
        ]
        if document_version is not None:
            must.append(self._match("document_version", document_version))
        return models.Filter(must=must)

    def count_document(self, organization_id: str, wiki_id: str, document_id: str, document_version: str | None = None) -> int:
        result = self.client.count(
            collection_name=self.settings.qdrant_collection,
            count_filter=self.document_filter(organization_id, wiki_id, document_id, document_version),
            exact=True,
        )
        return int(result.count)

    def count_document_version(
        self, organization_id: str, wiki_id: str, document_id: str, document_version: str
    ) -> int:
        return self.count_document(organization_id, wiki_id, document_id, document_version)

    def delete_document(self, organization_id: str, wiki_id: str, document_id: str, document_version: str | None = None) -> int:
        before = self.count_document(organization_id, wiki_id, document_id, document_version)
        self.client.delete(
            collection_name=self.settings.qdrant_collection,
            points_selector=models.FilterSelector(
                filter=self.document_filter(organization_id, wiki_id, document_id, document_version)
            ),
            wait=True,
        )
        return before

    def delete_obsolete_points(
        self, organization_id: str, wiki_id: str, document_id: str,
        document_version: str, keep_point_ids: list[str],
    ) -> int:
        filt = self.document_filter(organization_id, wiki_id, document_id, document_version)
        existing_ids: list[str] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.settings.qdrant_collection,
                scroll_filter=filt,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            existing_ids.extend(str(point.id) for point in points)
            if offset is None:
                break
        keep = set(keep_point_ids)
        obsolete = [point_id for point_id in existing_ids if point_id not in keep]
        if obsolete:
            self.client.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=obsolete,
                wait=True,
            )
        return len(obsolete)

    def _version_state_filter(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        *,
        state: str,
        document_version: str | None = None,
        exclude_version: str | None = None,
    ) -> models.Filter:
        base = self.document_filter(organization_id, wiki_id, document_id, document_version)
        must = list(base.must or [])
        must.append(self._match("version_state", state))
        must_not = []
        if exclude_version is not None:
            must_not.append(self._match("document_version", exclude_version))
        return models.Filter(must=must, must_not=must_not or None)

    @staticmethod
    def _numeric_version(value: str) -> int:
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError(f"Invalid stored document version: {value!r}")
        return int(value)

    def get_active_version(
        self, organization_id: str, wiki_id: str, document_id: str
    ) -> str | None:
        versions: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.settings.qdrant_collection,
                scroll_filter=self._version_state_filter(
                    organization_id, wiki_id, document_id, state="active"
                ),
                limit=256,
                offset=offset,
                with_payload=["document_version"],
                with_vectors=False,
            )
            versions.update(
                str(point.payload.get("document_version"))
                for point in points
                if point.payload and point.payload.get("document_version") is not None
            )
            if offset is None:
                break
        if not versions:
            return None
        return max(versions, key=self._numeric_version)

    def deactivate_other_versions(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        keep_version: str,
    ) -> int:
        filt = self._version_state_filter(
            organization_id,
            wiki_id,
            document_id,
            state="active",
            exclude_version=keep_version,
        )
        count = int(
            self.client.count(
                collection_name=self.settings.qdrant_collection,
                count_filter=filt,
                exact=True,
            ).count
        )
        if count:
            self.client.set_payload(
                collection_name=self.settings.qdrant_collection,
                payload={"version_state": "inactive"},
                points=filt,
                wait=True,
                ordering=models.WriteOrdering.STRONG,
            )
        return count

    def activate_version(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        document_version: str,
        *,
        replace_existing: bool,
    ) -> ActivationResult:
        """Guarded active-version switch; historical points are only relabeled."""

        candidate_number = self._numeric_version(document_version)
        current = self.get_active_version(organization_id, wiki_id, document_id)
        if current is not None and candidate_number < self._numeric_version(current):
            return ActivationResult(False, current, "newer-version-already-active")
        if current is not None and current != document_version and not replace_existing:
            return ActivationResult(False, current, "replacement-disabled")

        candidate_filter = self.document_filter(
            organization_id, wiki_id, document_id, document_version
        )
        other_active_filter = self._version_state_filter(
            organization_id,
            wiki_id,
            document_id,
            state="active",
            exclude_version=document_version,
        )
        deactivated = int(
            self.client.count(
                collection_name=self.settings.qdrant_collection,
                count_filter=other_active_filter,
                exact=True,
            ).count
        )
        operations = [
            models.SetPayloadOperation(
                set_payload=models.SetPayload(
                    payload={"version_state": "active"}, filter=candidate_filter
                )
            )
        ]
        if deactivated:
            operations.append(
                models.SetPayloadOperation(
                    set_payload=models.SetPayload(
                        payload={"version_state": "inactive"}, filter=other_active_filter
                    )
                )
            )
        try:
            self.client.batch_update_points(
                collection_name=self.settings.qdrant_collection,
                update_operations=operations,
                wait=True,
                ordering=models.WriteOrdering.STRONG,
            )
        except Exception:
            rollback = [
                models.SetPayloadOperation(
                    set_payload=models.SetPayload(
                        payload={"version_state": "inactive"}, filter=candidate_filter
                    )
                )
            ]
            if current is not None and current != document_version:
                rollback.append(
                    models.SetPayloadOperation(
                        set_payload=models.SetPayload(
                            payload={"version_state": "active"},
                            filter=self.document_filter(
                                organization_id, wiki_id, document_id, current
                            ),
                        )
                    )
                )
            try:
                self.client.batch_update_points(
                    collection_name=self.settings.qdrant_collection,
                    update_operations=rollback,
                    wait=True,
                    ordering=models.WriteOrdering.STRONG,
                )
            except Exception:
                logger.exception(
                    "Could not restore active version %s after activation failure", current
                )
            raise
        return ActivationResult(True, document_version, "activated", deactivated)

    def _search_filter(
        self, organization_id: str, wiki_id: str, filters: SearchFilters
    ) -> models.Filter:
        must: list[Any] = [
            self._match("organization_id", organization_id),
            self._match("wiki_id", wiki_id),
            self._match("version_state", "active"),
        ]
        if filters.document_ids:
            must.append(models.FieldCondition(key="document_id", match=models.MatchAny(any=filters.document_ids)))
        if filters.section_type_ids:
            must.append(models.FieldCondition(key="section_type_id", match=models.MatchAny(any=filters.section_type_ids)))
        if filters.tags:
            must.append(models.FieldCondition(key="tags", match=models.MatchAny(any=filters.tags)))
        if filters.language:
            must.append(self._match("language", filters.language))
        if filters.visibility:
            must.append(models.FieldCondition(key="visibility", match=models.MatchAny(any=filters.visibility)))
        if filters.acl_groups:
            must.append(models.FieldCondition(key="acl_groups", match=models.MatchAny(any=filters.acl_groups)))
        return models.Filter(must=must)

    def search(
        self,
        *,
        dense: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        organization_id: str,
        wiki_id: str,
        filters: SearchFilters,
        limit: int,
        mode: str,
    ) -> list[RetrievedPoint]:
        query_filter = self._search_filter(organization_id, wiki_id, filters)
        if mode == "hybrid" and self.settings.enable_sparse and sparse_indices:
            result = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                prefetch=[
                    models.Prefetch(
                        query=dense, using=self.settings.qdrant_dense_vector_name,
                        filter=query_filter, limit=limit,
                    ),
                    models.Prefetch(
                        query=models.SparseVector(indices=sparse_indices, values=sparse_values),
                        using=self.settings.qdrant_sparse_vector_name, filter=query_filter, limit=limit,
                    ),
                ],
                query=models.RrfQuery(rrf=models.Rrf(k=self.settings.rrf_k)),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        else:
            result = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=dense,
                using=self.settings.qdrant_dense_vector_name,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        return [
            RetrievedPoint(str(point.id), float(point.score), dict(point.payload or {}))
            for point in result.points
        ]

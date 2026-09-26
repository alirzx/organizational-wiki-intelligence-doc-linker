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
        except Exception:
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

    def upsert(self, chunks: list[EmbeddedChunk], *, embedding_model: str, chunker_version: str, indexed_at: str) -> None:
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

    def delete_stale_versions(self, organization_id: str, wiki_id: str, document_id: str, keep_version: str) -> int:
        filt = models.Filter(
            must=[
                self._match("organization_id", organization_id),
                self._match("wiki_id", wiki_id),
                self._match("document_id", document_id),
            ],
            must_not=[self._match("document_version", keep_version)],
        )
        before = int(self.client.count(
            collection_name=self.settings.qdrant_collection, count_filter=filt, exact=True
        ).count)
        if before:
            self.client.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=models.FilterSelector(filter=filt),
                wait=True,
            )
        return before

    def _search_filter(
        self, organization_id: str, wiki_id: str, filters: SearchFilters
    ) -> models.Filter:
        must: list[Any] = [
            self._match("organization_id", organization_id),
            self._match("wiki_id", wiki_id),
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

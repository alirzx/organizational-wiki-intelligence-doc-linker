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
    active_index_revision: str | None = None


@dataclass(frozen=True)
class ActiveRevision:
    document_version: str
    index_revision: str
    point_count: int


class ActiveStateInvariantError(RuntimeError):
    """The stored active points do not identify exactly one internal revision."""


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
            "index_revision": models.PayloadSchemaType.KEYWORD,
            "version_state": models.PayloadSchemaType.KEYWORD,
            "content_id": models.PayloadSchemaType.KEYWORD,
            "content_type": models.PayloadSchemaType.KEYWORD,
            "section_id": models.PayloadSchemaType.KEYWORD,
            "section_type_id": models.PayloadSchemaType.INTEGER,
            "language": models.PayloadSchemaType.KEYWORD,
            "tags": models.PayloadSchemaType.KEYWORD,
            "visibility": models.PayloadSchemaType.KEYWORD,
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

    def revision_filter(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        document_version: str,
        index_revision: str,
    ) -> models.Filter:
        base = self.document_filter(
            organization_id, wiki_id, document_id, document_version
        )
        return models.Filter(
            must=[*(base.must or []), self._match("index_revision", index_revision)]
        )

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

    def count_revision(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        document_version: str,
        index_revision: str,
    ) -> int:
        result = self.client.count(
            collection_name=self.settings.qdrant_collection,
            count_filter=self.revision_filter(
                organization_id, wiki_id, document_id, document_version, index_revision
            ),
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
        document_version: str, index_revision: str, keep_point_ids: list[str],
    ) -> int:
        filt = self.revision_filter(
            organization_id, wiki_id, document_id, document_version, index_revision
        )
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
        index_revision: str | None = None,
        exclude_revision: tuple[str, str] | None = None,
    ) -> models.Filter:
        base = self.document_filter(organization_id, wiki_id, document_id, document_version)
        must = list(base.must or [])
        must.append(self._match("version_state", state))
        if index_revision is not None:
            must.append(self._match("index_revision", index_revision))
        must_not: list[Any] = []
        if exclude_revision is not None:
            excluded_version, excluded_index_revision = exclude_revision
            must_not.append(models.Filter(must=[
                self._match("document_version", excluded_version),
                self._match("index_revision", excluded_index_revision),
            ]))
        return models.Filter(must=must, must_not=must_not or None)

    @staticmethod
    def _numeric_version(value: str) -> int:
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError(f"Invalid stored document version: {value!r}")
        return int(value)

    def get_active_state(
        self, organization_id: str, wiki_id: str, document_id: str
    ) -> ActiveRevision | None:
        states: dict[tuple[str, str], int] = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.settings.qdrant_collection,
                scroll_filter=self._version_state_filter(
                    organization_id, wiki_id, document_id, state="active"
                ),
                limit=256,
                offset=offset,
                with_payload=["document_version", "index_revision"],
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                version = str(payload.get("document_version") or "")
                index_revision = str(payload.get("index_revision") or "")
                try:
                    self._numeric_version(version)
                except ValueError as exc:
                    raise ActiveStateInvariantError(str(exc)) from exc
                if not index_revision:
                    raise ActiveStateInvariantError(
                        "Active Qdrant point is missing index_revision"
                    )
                identity = (version, index_revision)
                states[identity] = states.get(identity, 0) + 1
            if offset is None:
                break
        if not states:
            return None
        if len(states) != 1:
            identities = ", ".join(
                f"v{version}/{revision}" for version, revision in sorted(states)
            )
            raise ActiveStateInvariantError(
                f"Multiple active indexing revisions for document {document_id}: {identities}"
            )
        (version, index_revision), point_count = next(iter(states.items()))
        revision_count = self.count_revision(
            organization_id,
            wiki_id,
            document_id,
            version,
            index_revision,
        )
        if point_count != revision_count:
            raise ActiveStateInvariantError(
                "Active indexing revision is only partially active: "
                f"v{version}/{index_revision} has {point_count} active of "
                f"{revision_count} total points"
            )
        return ActiveRevision(version, index_revision, point_count)

    def get_active_version(
        self, organization_id: str, wiki_id: str, document_id: str
    ) -> str | None:
        active = self.get_active_state(organization_id, wiki_id, document_id)
        return active.document_version if active else None

    def deactivate_other_revisions(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        keep_version: str,
        keep_index_revision: str,
    ) -> int:
        filt = self._version_state_filter(
            organization_id,
            wiki_id,
            document_id,
            state="active",
            exclude_revision=(keep_version, keep_index_revision),
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

    def _verify_active_revision(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        document_version: str,
        index_revision: str,
        expected_point_count: int,
    ) -> ActiveRevision:
        active = self.get_active_state(organization_id, wiki_id, document_id)
        if active is None:
            raise ActiveStateInvariantError("Activation produced no active revision")
        if (
            active.document_version != document_version
            or active.index_revision != index_revision
        ):
            raise ActiveStateInvariantError(
                "Activation produced the wrong active revision: "
                f"expected v{document_version}/{index_revision}, got "
                f"v{active.document_version}/{active.index_revision}"
            )
        if active.point_count != expected_point_count:
            raise ActiveStateInvariantError(
                "Active candidate point count mismatch: "
                f"expected {expected_point_count}, got {active.point_count}"
            )
        return active

    def _restore_active_revision(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        previous: ActiveRevision | None,
    ) -> None:
        all_active = self._version_state_filter(
            organization_id, wiki_id, document_id, state="active"
        )
        operations = [
            models.SetPayloadOperation(
                set_payload=models.SetPayload(
                    payload={"version_state": "inactive"}, filter=all_active
                )
            )
        ]
        if previous is not None:
            operations.append(
                models.SetPayloadOperation(
                    set_payload=models.SetPayload(
                        payload={"version_state": "active"},
                        filter=self.revision_filter(
                            organization_id,
                            wiki_id,
                            document_id,
                            previous.document_version,
                            previous.index_revision,
                        ),
                    )
                )
            )
        self.client.batch_update_points(
            collection_name=self.settings.qdrant_collection,
            update_operations=operations,
            wait=True,
            ordering=models.WriteOrdering.STRONG,
        )
        restored = self.get_active_state(organization_id, wiki_id, document_id)
        if previous is None:
            if restored is not None:
                raise ActiveStateInvariantError(
                    "Rollback could not restore the previous zero-active state"
                )
            return
        if restored != previous:
            raise ActiveStateInvariantError(
                "Rollback did not restore the previous active revision"
            )

    def _rollback_after_activation_failure(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        previous: ActiveRevision | None,
    ) -> None:
        try:
            self._restore_active_revision(
                organization_id, wiki_id, document_id, previous
            )
        except Exception as rollback_error:
            logger.critical(
                "Could not restore active revision %s after activation failure",
                previous,
                exc_info=True,
            )
            raise ActiveStateInvariantError(
                "Activation failed and rollback could not restore a valid active state"
            ) from rollback_error

    def activate_version(
        self,
        organization_id: str,
        wiki_id: str,
        document_id: str,
        document_version: str,
        index_revision: str,
        *,
        replace_existing: bool,
        expected_point_count: int,
    ) -> ActivationResult:
        """Guard and verify an internal revision switch without deleting history."""

        candidate_number = self._numeric_version(document_version)
        if not index_revision:
            raise ValueError("index_revision must not be empty")
        if expected_point_count <= 0:
            raise ValueError("expected_point_count must be positive")
        candidate_count = self.count_revision(
            organization_id,
            wiki_id,
            document_id,
            document_version,
            index_revision,
        )
        if candidate_count != expected_point_count:
            raise ActiveStateInvariantError(
                "Candidate point count changed before activation: "
                f"expected {expected_point_count}, got {candidate_count}"
            )

        current = self.get_active_state(organization_id, wiki_id, document_id)
        if current is not None and candidate_number < self._numeric_version(
            current.document_version
        ):
            return ActivationResult(
                False,
                current.document_version,
                "newer-version-already-active",
                active_index_revision=current.index_revision,
            )
        candidate_identity = (document_version, index_revision)
        if current is not None and (
            current.document_version, current.index_revision
        ) == candidate_identity:
            self._verify_active_revision(
                organization_id,
                wiki_id,
                document_id,
                document_version,
                index_revision,
                expected_point_count,
            )
            return ActivationResult(
                True,
                document_version,
                "already-active",
                active_index_revision=index_revision,
            )
        if current is not None and not replace_existing:
            return ActivationResult(
                False,
                current.document_version,
                "replacement-disabled",
                active_index_revision=current.index_revision,
            )

        candidate_filter = self.revision_filter(
            organization_id,
            wiki_id,
            document_id,
            document_version,
            index_revision,
        )
        other_active_filter = self._version_state_filter(
            organization_id,
            wiki_id,
            document_id,
            state="active",
            exclude_revision=candidate_identity,
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
            self._rollback_after_activation_failure(
                organization_id, wiki_id, document_id, current
            )
            raise
        try:
            self._verify_active_revision(
                organization_id,
                wiki_id,
                document_id,
                document_version,
                index_revision,
                expected_point_count,
            )
        except Exception:
            self._rollback_after_activation_failure(
                organization_id, wiki_id, document_id, current
            )
            raise
        return ActivationResult(
            True,
            document_version,
            "activated",
            deactivated,
            index_revision,
        )

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

# Stage 3 architecture

## Published indexing graph

```text
POST /api/v1/documents/index-ready
  -> IndexReadyRequest: strict versions/vN/document.json contract
  -> MinIO stat: capture accepted ETag/size
  -> JobStore.create_or_get: immutable fingerprint + pipeline identity
  -> Celery index_document
  -> per-tenant/document Redis lock
  -> IndexingService.process
  -> MinIOStore.download_verified: accepted ETag + optional SHA256 + mutation check
  -> ArtifactAdapterFactory
  -> PublishedDocumentAdapter
  -> CanonicalDocument / CanonicalContent
  -> SemanticChunker
  -> BGE-M3 document embedding (1024 dense + learned sparse)
  -> QdrantStore.upsert(version_state=inactive)
  -> delete_obsolete_points(same document_version only)
  -> count_document_version verification
  -> QdrantStore.activate_version numeric guard
  -> version-scoped Linking audit
  -> succeeded job + Backend callback
```

The lock covers verification and activation. It prevents concurrent changes, while the numeric comparison against Qdrant's currently active version prevents sequential late workers from downgrading search state.

## Activation invariant

The candidate is inactive until all points are written and the exact version count is verified. Activation uses ordered Qdrant payload operations: candidate points become active first, then other active versions become inactive. Historical points are relabeled, not deleted.

```text
active=v2, candidate=v3 succeeds  -> v2 inactive, v3 active
active=v2, candidate=v3 fails     -> v2 remains active, v3 inactive/partial
active=v3, delayed candidate=v2   -> v3 remains active, v2 inactive
```

`replace_existing=false` leaves a newer candidate inactive when another version is already active. The first successfully verified version becomes active even with no prior active version. Re-indexing the exact same version retains same-version obsolete-point cleanup.

## Retrieval graph

```text
POST /api/v1/search
  -> RetrievalService.search
  -> BGE-M3 query dense + sparse encoding
  -> Qdrant filter:
       organization_id + wiki_id + version_state=active
       + Backend document/tag/language/visibility/ACL filters
  -> dense/sparse prefetch + RRF
  -> candidate pool (default 40)
  -> BGE-reranker-v2-m3
  -> SearchResult with generic published-content references
```

There is no ordinary historical-search mode. Inactive versions are physically preserved but cannot leak into normal search.

## Source model

The primary adapter emits generic source concepts:

```text
source_kind=published_snapshot
content_id (optional stable Backend/editor ID)
content_type
content_title
content_order
source_metadata
```

When a stable ID exists it participates in deterministic point identity and is returned unchanged. When it does not exist, Stage 3 does not fabricate a public source ID; deterministic point identity falls back internally to content type/order/hash. Optional legacy Stage-2 page, block, classifier, and section metadata remains in the payload only when an old adapter actually supplies it.

## Storage ownership

```text
Shared MinIO documents/{id}/versions/vN/document.json       Backend-owned, read-only
Shared MinIO documents/{id}/Linking/versions/vN/...         Stage-3 audit ownership
Shared Redis                                                jobs/locks/idempotency/generation + Celery
Dedicated Qdrant wiki_chunks_v1                             vectors + active search metadata
```

Explicit `DELETE /api/v1/documents/{document_id}` may delete all indexed Qdrant versions or one version. Replacement never calls delete. Deleting an active version does not automatically reactivate an older version because the Backend contract does not define that policy.

## Published snapshot schema boundary

No Backend serializer or fixture for `document.json` exists in this repository or the available neighboring Stage-2 project. Until the Backend schema is supplied, all tolerance is confined to `PublishedDocumentAdapter`: an object (optionally under `document`) with ordered `content`, `contents`, `sections`, or `blocks`; a text `content` is also accepted. Items may provide `id`/content/section/block/node IDs, type, title, order, text, metadata/attrs, or nested editor text nodes. This temporary compatibility surface must be replaced or narrowed when the authoritative serializer is available; no guessed fields leak into Qdrant lifecycle code.

## Security boundaries

- The request path must exactly match `documents/{document_id}/versions/v{positive_integer}/document.json`.
- `uri`, when present, must exactly match `s3://{bucket}/{object_key}`.
- The bucket must equal the deployment-configured Wiki bucket.
- Object traversal is rejected.
- The API persists the live ETag accepted at request time; the worker must download that fingerprint.
- Network destinations and credentials remain deployment configuration.

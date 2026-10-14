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
  -> strict identity cross-check + inline data-image sanitization
  -> CanonicalDocument / CanonicalContent
  -> SemanticChunker
  -> shared title + body semantic passage
  -> BGE-M3 document embedding (1024 dense + learned sparse)
  -> QdrantStore.upsert(shadow index_revision, version_state=inactive)
  -> delete_obsolete_points(same candidate revision only)
  -> count_revision verification
  -> QdrantStore.activate_version numeric guard + revision switch
  -> single-active-revision/count post-condition verification
  -> version-scoped Linking audit
  -> succeeded job + Backend callback
```

The lock covers verification and activation. It prevents concurrent changes, while the numeric comparison against Qdrant's currently active version prevents sequential late workers from downgrading search state.

## Published identity and internal revision

Backend identity remains `(document_id, document_version)`. Stage 3 assigns `index_revision=job_id` internally and includes it in deterministic point identity. A Celery retry uses the same job and revision; a new accepted indexing job gets a new revision. The public request, callback correlation, MinIO snapshot path, and version audit path do not require or expose this as Backend identity.

## Activation invariant

The candidate is inactive until all points are written and its exact revision count is verified. Active state is the tuple `(document_version, index_revision)`, and inspection rejects multiple distinct active tuples instead of choosing the largest version. Activation uses ordered Qdrant payload operations, then explicitly verifies that exactly one complete candidate revision is active. Qdrant ordered writes are not treated as a transaction: an operation or post-condition failure marks the candidate inactive, restores the exact prior version/revision, verifies restoration, and raises. A failed restoration is a critical indexing error and cannot produce a success callback.

```text
active=v2/rev-A, candidate=v3/rev-B succeeds -> only v3/rev-B active
active=v3/rev-A, candidate=v3/rev-B fails    -> v3/rev-A remains active
active=v3/rev-X, delayed v2/rev-Y             -> v3/rev-X remains active
```

`replace_existing=false` leaves a candidate inactive when another revision is active. The first successfully verified version becomes active even with no prior active version. Cleanup and verification are scoped to the shadow revision, so a same-version candidate cannot overwrite or delete the searchable revision before activation.

## Retrieval graph

```text
POST /api/v1/search
  -> RetrievalService.search
  -> BGE-M3 query dense + sparse encoding
  -> Qdrant filter:
       organization_id + wiki_id + version_state=active
       + Backend document/tag/language/visibility filters
  -> dense/sparse prefetch + RRF
  -> candidate pool (default 40)
  -> shared title + body semantic passage
  -> BGE-reranker-v2-m3
  -> SearchResult with generic published-content references
```

There is no ordinary historical-search mode. Inactive versions are physically preserved but cannot leak into normal search.

## Source model

The primary adapter emits generic source concepts:

```text
source_kind=published_snapshot
content_id=null
content_type=section
content_title=content.sections map key
content_order=serialized map order (1..N)
source_metadata.section_key=original map key
```

The production Backend snapshot provides no section ID, so Stage 3 does not fabricate one; deterministic point identity falls back internally to content type/order/body hash. Optional legacy Stage-2 page, block, classifier, stable-ID, and section metadata remains in the payload only when an old adapter actually supplies it. The section title prefixes the model-facing passage for embedding and reranking, but stored `text`, `content_hash`, and `SearchResult.text` remain body-only.

## Storage ownership

```text
Shared MinIO documents/{id}/versions/vN/document.json       Backend-owned, read-only
Shared MinIO documents/{id}/Linking/versions/vN/...         Stage-3 audit ownership
Shared Redis                                                jobs/locks/idempotency/generation + Celery
Dedicated Qdrant wiki_chunks_v1                             vectors + active search metadata
```

Explicit `DELETE /api/v1/documents/{document_id}` may delete all indexed Qdrant versions or one version. Replacement never calls delete. Deleting an active version does not automatically reactivate an older version because the Backend contract does not define that policy.

## Published snapshot schema boundary

The production schema is explicit. Top-level `source_document` and
`content.source_document_id` are both Backend `Document.id` and must match the
request `document_id`. `version_number` must match `document_version`.
Top-level `id` is `WikiDocument.id` metadata and is never used as Stage-3
identity. `content.sections` is an insertion-ordered string-to-string map; each
entry becomes one `CanonicalContent` before chunk splitting. Snapshot title is
canonical when non-empty, with the request title as fallback. Unknown harmless
metadata is allowed and preserved under `document_metadata.backend_snapshot`;
invalid identity, version, section containers, or non-string section bodies are
rejected without coercion.

Backend owners, status, publication/action state, hyperlink rebuild state, and
timestamps remain non-semantic metadata. `owners` never becomes `acl_groups`;
request visibility and ACL fields remain authoritative. Inline HTML/Markdown
and bare base64 data-image URIs are removed before token counting or any RAG
stage. Normal HTTP/HTTPS text links are retained.

## Security boundaries

- The request path must exactly match `documents/{document_id}/versions/v{positive_integer}/document.json`.
- `uri`, when present, must exactly match `s3://{bucket}/{object_key}`.
- The bucket must equal the deployment-configured Wiki bucket.
- Object traversal is rejected.
- The API persists the live ETag accepted at request time; the worker must download that fingerprint.
- Network destinations and credentials remain deployment configuration.

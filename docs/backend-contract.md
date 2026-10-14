# Stage 3 API contract

Base path: `/api/v1`. `document_id` is Backend `Document.id`; `document_version` is `WikiDocument.version_number`. Backend `WikiDocument.id` is not part of this contract.

## POST /documents/index-ready

```json
{
  "document_id": "64",
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "document_version": "2",
  "artifact": {
    "bucket": "media",
    "object_key": "documents/64/versions/v2/document.json",
    "uri": "s3://media/documents/64/versions/v2/document.json",
    "content_type": "application/json",
    "etag": null,
    "sha256": null
  },
  "document": {
    "title": "Security Policy",
    "source_filename": "security-policy.pdf",
    "language": "fa",
    "tags": ["policy"],
    "visibility": "internal",
    "acl_groups": ["security-team"],
    "extra": {}
  },
  "replace_existing": true
}
```

`document_version` is a JSON string for compatibility but must match `[1-9][0-9]*`. The object key must be exactly `documents/{document_id}/versions/v{document_version}/document.json`. The configured bucket must match; URI mismatches, path traversal, alternate filenames, Stage-2 paths, and arbitrary version labels are rejected.

At acceptance, Stage 3 stats the object and persists the live ETag into the durable job payload. A caller-supplied ETag must match. The worker verifies that accepted ETag, optional SHA256, size limits, and mutation-during-download. Missing objects fail before job creation with `artifact-not-found`.

HTTP 202:

```json
{
  "job_id": "f4a2c9",
  "document_id": "64",
  "document_version": "2",
  "status": "queued",
  "start_time": "2026-10-13T10:00:00Z"
}
```

A duplicate immutable identity reuses an existing non-failed job. Identity independently includes the object key, accepted live ETag, supplied SHA256 when present, accepted size, tenant/wiki/document/version, generation, pipeline/chunker versions, embedding model, and replacement flag. Failed jobs remain retryable.

Stage 3 uses the resulting `job_id` as an internal `index_revision`. Backend does not send this value and published identity remains `(document_id, document_version)`. The internal revision permits safe shadow re-indexing of the same published version.

`replace_existing=true` means activate this verified version and deactivate the prior active search version. It never deletes a historical Qdrant version or any MinIO snapshot.

## Immutable `document.json`

The authoritative snapshot shape is:

```json
{
  "id": 58,
  "title": "یک سند تستی",
  "content": {
    "sections": {
      "جایگاه اول": "متن بخش اول",
      "جایگاه دوم": "متن بخش دوم"
    },
    "source_document_id": 270
  },
  "position": "جایگاه اول",
  "owners": [1, 2],
  "status": "published",
  "version_number": 1,
  "source_document": 270,
  "source_version": null,
  "published_at": "2026-10-14T00:00:00Z",
  "available_actions": {},
  "hyperlink_rebuild": {},
  "created_at": "2026-10-05T12:10:45.703546Z",
  "updated_at": "2026-10-14T00:00:00Z"
}
```

For this example, request identity is `document_id="270"` and
`document_version="1"`; `id=58` is `WikiDocument.id` metadata only. Both source
document fields and the numeric version are required and cross-validated. The
sections map retains serialized order. Each non-empty cleaned string becomes a
`section` with a null `content_id`, the map key as `content_title`, and the key
also in `source_metadata.section_key`. Stage 3 does not invent section IDs.

Top-level operational fields are preserved as non-semantic
`backend_snapshot` metadata. `owners` is not an ACL source. Snapshot
`status`/`published_at` do not gate indexing because the authenticated
`index-ready` call is the trigger. Data-image HTML, Markdown, and bare base64
URIs are stripped before tokenization, chunking, embedding, audit, Qdrant, and
reranking; ordinary HTTP/HTTPS links remain.

## GET /jobs/{job_id}

Index jobs expose `job_id`, `document_id`, and `document_version` at top level. Status proceeds through:

```text
queued -> downloading -> parsing -> chunking -> embedding
       -> indexing -> verifying -> succeeded / failed
```

Celery may expose `retrying` between attempts. Callback delivery fields are independent of indexing success.

## POST /search

The request contract is unchanged: query, organization/wiki tenant IDs, optional limit/candidate/mode, and Backend-derived document/tag/language/visibility/ACL filters. Ordinary search always adds `version_state=active` internally.

Published results expose generic references:

```json
{
  "document_id": "64",
  "document_version": "2",
  "content_id": null,
  "content_type": "section",
  "content_title": "Access policy",
  "content_order": 4,
  "source_metadata": {"section_key": "Access policy"},
  "chunk_index": 1,
  "artifact_key": "documents/64/versions/v2/document.json",
  "content_hash": "..."
}
```

Legacy `section_*`, page, source-block, and classifier fields remain optional for older Stage-2-derived points. They remain null/absent for generic published content unless actual legacy Stage-2 provenance exists.

## DELETE /documents/{document_id}

```json
{
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "document_version": null,
  "request_id": "optional-event-id"
}
```

Null version explicitly deletes all indexed versions; a version deletes only that Qdrant version. Publication replacement never calls this endpoint. Deleting the active version does not implicitly reactivate history.

## Callbacks

Success:

```json
{
  "event": "document-linking-indexed",
  "status": "succeeded",
  "job_id": "f4a2c9",
  "document_id": "64",
  "document_version": "2",
  "result": {}
}
```

Failure:

```json
{
  "event": "document-linking-failed",
  "status": "failed",
  "job_id": "f4a2c9",
  "document_id": "64",
  "document_version": "2",
  "error": {"type": "RuntimeError", "message": "indexing failed"}
}
```

Callback failure never rolls back successfully indexed data. Callback delivery never changes Qdrant active state.

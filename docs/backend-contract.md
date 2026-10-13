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

A duplicate immutable identity reuses an existing non-failed job. Identity includes tenant/wiki/document/version, accepted artifact fingerprint and size, generation, pipeline/chunker versions, and embedding model. Failed jobs remain retryable.

`replace_existing=true` means activate this verified version and deactivate the prior active search version. It never deletes a historical Qdrant version or any MinIO snapshot.

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
  "content_id": "stable-backend-id-or-null",
  "content_type": "section",
  "content_title": "Access policy",
  "content_order": 4,
  "source_metadata": {},
  "chunk_index": 1,
  "artifact_key": "documents/64/versions/v2/document.json",
  "content_hash": "..."
}
```

Legacy `section_*`, page, source-block, and classifier fields remain optional for older Stage-2-derived points. Stage 3 does not invent them for published snapshots.

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

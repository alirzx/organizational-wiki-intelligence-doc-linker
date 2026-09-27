# Backend ↔ Stage 3 Document Linker integration guide

This document is the Backend-facing integration contract for Stage 3. It is derived from the current FastAPI routes and Pydantic schemas and intentionally does not introduce extra endpoints.

## 1. Service base and authentication

Stage 3 exposes the Backend-facing API on port `8090` by default.

Protected endpoints accept either:

```http
X-API-Key: <DOC_LINKER_API_AUTH_TOKEN>
```

or:

```http
Authorization: Bearer <DOC_LINKER_API_AUTH_TOKEN>
```

The Backend does **not** need `DOC_LINKER_MODEL_SERVICE_TOKEN`; that token is only for private communication inside Stage 3 between API/worker and the internal model service.

Health endpoints are not protected.

## 2. Responsibilities

### Backend owns

- canonical `document_id`;
- `organization_id` and `wiki_id`;
- document versioning;
- user authentication and authorization;
- deciding which completed Stage-2 artifact should be indexed;
- calling Stage 3 after Template Maker completion;
- calling delete when a document/version is removed;
- passing search ACL/visibility filters derived from the authenticated user;
- displaying the references returned by search;
- optionally receiving Stage-3 terminal callbacks.

### Stage 3 owns

- validating and downloading the supplied Stage-2 artifact from shared MinIO;
- parsing `sections.json`, `sections.docx`, or `sections.md`;
- semantic chunking;
- BGE-M3 embedding;
- Qdrant indexing/update/delete;
- Redis/Celery asynchronous jobs;
- retrieval and BGE reranking;
- Stage-3 audit artifacts under `documents/{document_id}/Linking/`;
- callback delivery state.

## 3. Preferred Stage-2 handoff

Preferred artifact:

```text
media/documents/{document_id}/Sectioning/ocr/sections.json
```

Preferred manifest:

```text
media/documents/{document_id}/Sectioning/ocr/manifest.json
```

`sections.json` is preferred because it contains the full Stage-2 text plus section/page/source-block/classifier provenance. `sections.docx` and `sections.md` are supported compatibility inputs.

The Backend sends a MinIO object reference, not file bytes and not a MinIO endpoint URL. Stage 3 already knows the shared MinIO endpoint from deployment configuration.

---

# 4. POST /api/v1/documents/index-ready

Use this endpoint when Template Maker has completed and the document is ready to become searchable.

The operation is asynchronous. A successful request returns HTTP `202`; chunking, embedding and Qdrant indexing continue in Celery.

## Request

```json
{
  "document_id": "64",
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "document_version": "1",
  "artifact": {
    "bucket": "media",
    "object_key": "documents/64/Sectioning/ocr/sections.json",
    "uri": "s3://media/documents/64/Sectioning/ocr/sections.json",
    "content_type": "application/json",
    "etag": null,
    "sha256": null
  },
  "manifest": {
    "bucket": "media",
    "object_key": "documents/64/Sectioning/ocr/manifest.json",
    "etag": null,
    "sha256": null
  },
  "document": {
    "title": "Document title",
    "source_filename": "source.pdf",
    "language": "fa",
    "tags": ["policy"],
    "visibility": "internal",
    "acl_groups": ["security-team"],
    "extra": {}
  },
  "replace_existing": true
}
```

### Required fields

- `document_id`
- `organization_id`
- `wiki_id`
- `artifact`
- `document.title`

`document_version` defaults to `"1"` when omitted.

### Artifact rules

The artifact must:

- use the configured MinIO bucket;
- exist under `documents/{document_id}/Sectioning/`;
- end with exactly one of:
  - `sections.json`
  - `sections.docx`
  - `sections.md`

If `uri` is provided, it must exactly match:

```text
s3://{bucket}/{object_key}
```

If `manifest` is provided, it must be the sibling `manifest.json` beside the selected artifact. Stage 3 validates that the manifest succeeded, belongs to the same document and lists the requested artifact.

`etag` and `sha256` are optional. Supplying either gives stronger version/integrity validation.

### `replace_existing`

- `true`: index and verify the new version first, then remove older versions of the same document.
- `false`: keep older document versions indexed as well.

## HTTP 202 response

```json
{
  "job_id": "f4a2c9...",
  "status": "queued",
  "start_time": "2026-09-27T07:00:00Z"
}
```

A duplicate request for the same active/successful index identity may return the existing job instead of creating duplicate vectors.

## Terminal index success callback

If `DOC_LINKER_CALLBACK_URL` is configured, Stage 3 POSTs:

```json
{
  "event": "document-linking-indexed",
  "status": "succeeded",
  "job_id": "f4a2c9...",
  "document_id": "64",
  "result": {
    "document_id": "64",
    "document_version": "1",
    "organization_id": "org-1",
    "wiki_id": "wiki-main",
    "collection": "wiki_chunks_v1",
    "chunk_count": 12,
    "deleted_stale_points": 0,
    "artifact_sha256": "...",
    "audit_outputs": [
      "documents/64/Linking/chunks.json",
      "documents/64/Linking/index-manifest.json",
      "documents/64/Linking/jobs/f4a2c9.../success.json"
    ],
    "audit_error": null,
    "timings": {
      "download_seconds": 0.1,
      "parsing_seconds": 0.02,
      "chunking_seconds": 0.2,
      "embedding_seconds": 0.5,
      "qdrant_seconds": 0.08,
      "total_seconds": 0.9
    }
  }
}
```

The exact timing values and chunk count naturally vary by document.

## Terminal index failure callback

```json
{
  "event": "document-linking-failed",
  "status": "failed",
  "job_id": "f4a2c9...",
  "document_id": "64",
  "error": {
    "type": "RuntimeError",
    "message": "..."
  }
}
```

---

# 5. GET /api/v1/jobs/{job_id}

Use this endpoint to poll asynchronous index/delete jobs.

## Example queued response

```json
{
  "job_id": "f4a2c9...",
  "operation": "index",
  "status": "queued",
  "start_time": "2026-09-27T07:00:00Z",
  "updated_time": "2026-09-27T07:00:00Z",
  "payload": {},
  "error": null,
  "result": null,
  "callback_delivered": false,
  "callback_error": null,
  "retry_count": 0
}
```

The real `payload` contains the original operation request.

Index job statuses can include:

```text
queued
retrying
downloading
parsing
chunking
embedding
indexing
verifying
succeeded
failed
```

Delete jobs additionally use:

```text
deleting
```

On success, `result` contains the terminal operation result. On failure, `error` contains `type` and `message`.

`callback_delivered` and `callback_error` describe callback delivery only. A callback failure does not roll back a successful Qdrant operation.

---

# 6. POST /api/v1/search

Use this synchronously for Wiki search.

Stage 3 embeds the query, searches Qdrant, performs hybrid dense+sparse retrieval by default, reranks candidates with `BAAI/bge-reranker-v2-m3`, and returns referenced chunks.

## Request

```json
{
  "query": "مسئولیت‌های تیم امنیت چیست؟",
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "limit": 5,
  "candidate_limit": 40,
  "mode": "hybrid",
  "filters": {
    "document_ids": [],
    "section_type_ids": [],
    "tags": ["policy"],
    "language": "fa",
    "visibility": ["internal"],
    "acl_groups": ["security-team"]
  }
}
```

### Required

- `query`
- `organization_id`
- `wiki_id`

### Optional

- `limit`: number of final returned chunks. Default is 5; deployment currently limits it to 20.
- `candidate_limit`: candidate pool before reranking. Default is 40.
- `mode`: `dense` or `hybrid`; default is deployment configuration, currently `hybrid`.
- `filters`: optional retrieval filters.

The Backend is responsible for translating the authenticated user's permissions into `visibility`/`acl_groups`; Stage 3 does not determine user membership itself.

## Response

```json
{
  "query": "مسئولیت‌های تیم امنیت چیست؟",
  "mode": "hybrid",
  "candidate_count": 18,
  "results": [
    {
      "rank": 1,
      "score": 0.91,
      "retrieval_score": 0.72,
      "rerank_score": 0.91,
      "text": "...relevant chunk text...",
      "document_id": "64",
      "document_version": "1",
      "document_title": "Document title",
      "source_filename": "source.pdf",
      "section_id": "section-001",
      "section_type_id": 11,
      "section_title": "سایر / نامرتبط با طبقات",
      "section_order": 1,
      "section_confidence": 0.85,
      "section_classifier": "mixed",
      "page_start": 1,
      "page_end": 4,
      "source_blocks": [1, 2, 3],
      "chunk_index": 0,
      "artifact_bucket": "media",
      "artifact_key": "documents/64/Sectioning/ocr/sections.json",
      "content_hash": "...",
      "document_metadata": {}
    }
  ],
  "timings": {
    "embedding_seconds": 0.01,
    "retrieval_seconds": 0.02,
    "rerank_seconds": 0.04,
    "total_seconds": 0.07
  }
}
```

Important references for the Front are:

- `document_id`
- `document_title`
- `source_filename`
- `section_id`
- `section_title`
- `page_start` / `page_end`
- `source_blocks`
- `chunk_index`
- `artifact_key`

Page references are currently inherited from Stage-2 section ranges, not exact character-level page offsets.

---

# 7. DELETE /api/v1/documents/{document_id}

Use this when the product removes a document or one indexed document version.

The delete operation is asynchronous and returns HTTP `202`.

## Request

```http
DELETE /api/v1/documents/64
```

Body:

```json
{
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "document_version": null,
  "request_id": "backend-delete-event-123"
}
```

### Behavior

- `document_version: null` deletes all indexed versions of that document within the organization/wiki scope.
- a specific version deletes only that version.
- `request_id` is optional but strongly recommended. Reuse the same stable `request_id` when retrying the same Backend delete event.

## HTTP 202 response

```json
{
  "job_id": "8c05ab...",
  "status": "queued",
  "start_time": "2026-09-27T07:05:00Z"
}
```

## Terminal delete success callback

```json
{
  "event": "document-linking-deleted",
  "status": "succeeded",
  "job_id": "8c05ab...",
  "document_id": "64",
  "document_version": null,
  "result": {
    "document_id": "64",
    "document_version": null,
    "organization_id": "org-1",
    "wiki_id": "wiki-main",
    "deleted_points": 12,
    "document_generation": 1,
    "duration_seconds": 0.05
  }
}
```

## Terminal delete failure callback

```json
{
  "event": "document-linking-delete-failed",
  "status": "failed",
  "job_id": "8c05ab...",
  "document_id": "64",
  "document_version": null,
  "error": {
    "type": "RuntimeError",
    "message": "..."
  }
}
```

---

# 8. Health endpoints

## GET /health/live

Response:

```json
{
  "status": "alive"
}
```

This only proves the API process is alive.

## GET /health/ready

Healthy response:

```json
{
  "status": "ready",
  "minio": true,
  "redis": true,
  "qdrant": true,
  "models": true
}
```

If one or more dependencies are unavailable, the endpoint returns HTTP `503` with:

```json
{
  "status": "not-ready",
  "minio": true,
  "redis": false,
  "qdrant": true,
  "models": true
}
```

---

# 9. Common HTTP failure behavior

Before an async job is accepted, Backend may receive:

- `400`: invalid bucket/document/path/limit contract;
- `401`: missing or invalid service authentication;
- `404`: Stage-2 artifact or job not found;
- `409`: supplied artifact ETag does not match the live MinIO object;
- `422`: request schema validation failure;
- `503`: Redis/Celery/search/dependency failure.

Worker failures after HTTP `202` are represented in the job state and optional terminal callback rather than changing the already-returned HTTP response.

---

# 10. Backend integration flow summary

```text
Template Maker completed
       ↓
Backend POST /api/v1/documents/index-ready
       ↓ 202 + job_id
Stage 3 indexes asynchronously
       ↓
Backend GET /api/v1/jobs/{job_id} (optional polling)
       or
Backend receives terminal callback

User search
       ↓
Backend authenticates user and derives allowed ACL filters
       ↓
Backend POST /api/v1/search
       ↓
Stage 3 returns top referenced chunks
       ↓
Backend/Front displays result + document/section/page reference

Document deleted
       ↓
Backend DELETE /api/v1/documents/{document_id}
       ↓ 202 + job_id
Stage 3 removes Qdrant points asynchronously
       ↓
polling or terminal callback
```

## Backend minimum implementation checklist

1. Configure the Stage-3 base URL and `DOC_LINKER_API_AUTH_TOKEN` equivalent on the Backend side.
2. After successful Template Maker completion, call `POST /api/v1/documents/index-ready` with the selected Stage-2 artifact reference.
3. Persist/use the returned `job_id` if the UI needs processing state.
4. Optionally poll `GET /api/v1/jobs/{job_id}` or expose the configured callback endpoint.
5. For search, always send the correct `organization_id`, `wiki_id`, and authorization-derived filters.
6. Display document/section/page references returned by `/search`.
7. On document removal, call `DELETE /api/v1/documents/{document_id}` and use a stable `request_id` for retries.
8. Do not send MinIO credentials, MinIO endpoint, Qdrant details, Redis details, model-service token, embedding settings or reranker settings. Those are Stage-3 deployment concerns.

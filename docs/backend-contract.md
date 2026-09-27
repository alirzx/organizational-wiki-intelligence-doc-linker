# Stage 3 API contract

Base path: `/api/v1`.

Protected endpoints accept either `X-API-Key: <DOC_LINKER_API_AUTH_TOKEN>` or `Authorization: Bearer <DOC_LINKER_API_AUTH_TOKEN>`. The service never accepts arbitrary MinIO endpoints or Backend callback URLs in requests.

## Index a completed Stage-2 artifact

`POST /api/v1/documents/index-ready` returns HTTP `202` after request/artifact validation and durable Redis job reservation. It does not wait for chunking, embedding or Qdrant.

```json
{
  "document_id": "59",
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "document_version": "3",
  "artifact": {
    "bucket": "media",
    "object_key": "documents/59/Sectioning/ocr/sections.json",
    "uri": "s3://media/documents/59/Sectioning/ocr/sections.json",
    "content_type": "application/json",
    "etag": null,
    "sha256": null
  },
  "manifest": {
    "bucket": "media",
    "object_key": "documents/59/Sectioning/ocr/manifest.json",
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

Rules: bucket must be the configured Wiki bucket; key must be under `documents/{document_id}/Sectioning/`; accepted artifacts are `sections.json`, `sections.docx`, `sections.md`; optional `uri` must match bucket/key exactly; optional manifest must be sibling `manifest.json`, have `status=succeeded`, match document ID and list the artifact. `sections.json` is preferred. `replace_existing=true` removes older versions only after the new version is verified.

Response:

```json
{"job_id":"...","status":"queued","start_time":"..."}
```

## Job status

`GET /api/v1/jobs/{job_id}`. Index statuses include `queued`, `downloading`, `parsing`, `chunking`, `embedding`, `indexing`, `verifying`, `retrying`, `succeeded`, `failed`. Delete additionally uses `deleting`.

## Search

`POST /api/v1/search`

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

`organization_id` and `wiki_id` are mandatory. Mode is `dense` or `hybrid`. Default final limit is 5 and configured maximum is 20. Each result contains scores, text, document/version/title/source filename, section identity/type/order/classifier/confidence, inherited page range/source blocks, chunk index, artifact key/hash and document metadata, plus timing fields.

## Delete document embeddings

`DELETE /api/v1/documents/{document_id}` body:

```json
{"organization_id":"org-1","wiki_id":"wiki-main","document_version":null,"request_id":"optional-backend-event-id"}
```

Null version deletes all indexed versions; a version deletes only that version. `request_id` is recommended for idempotent Backend retries. Operation is asynchronous and returns HTTP `202`.

## Health

`GET /health/live` and `GET /health/ready`. Readiness checks MinIO, Redis, Qdrant and the selected internal model-service.

## Callback contract

Callback URL/token are deployment configuration. Callback failure never rolls back a verified Qdrant operation; job state exposes `callback_delivered` and `callback_error`.

Index success uses event `document-linking-indexed`, status `succeeded`, `job_id`, `document_id` and a `result` object containing collection, chunk count, deleted stale points, artifact SHA256, audit outputs/error and timings. Index failure uses `document-linking-failed`. Delete success uses `document-linking-deleted`; delete failure uses `document-linking-delete-failed`.

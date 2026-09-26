# Backend contract

All protected endpoints accept either `X-API-Key: <token>` or `Authorization: Bearer <token>`.
The service never accepts an arbitrary callback URL from requests; callback destination is deployment configuration.

## Index a completed Stage-2 artifact

`POST /api/v1/documents/index-ready` returns HTTP 202.

```json
{
  "document_id": "59",
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "document_version": "3",
  "artifact": {
    "bucket": "media",
    "object_key": "documents/59/Sectioning/ocr/sections.json",
    "content_type": "application/json",
    "etag": "optional",
    "sha256": "optional-64-hex"
  },
  "manifest": {
    "bucket": "media",
    "object_key": "documents/59/Sectioning/ocr/manifest.json"
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

The preferred artifact is `sections.json`; DOCX and Markdown are accepted fallback adapters.
If `manifest` is supplied, Stage 3 requires `status=succeeded` and verifies that the requested artifact appears in its outputs.

Response:
```json
{"job_id":"...","status":"queued","start_time":"..."}
```

## Job status
`GET /api/v1/jobs/{job_id}` returns queued/downloading/parsing/chunking/embedding/indexing/verifying/retrying/succeeded/failed.

## Search
`POST /api/v1/search` is synchronous. `organization_id` and `wiki_id` are mandatory tenant boundaries.
The default final limit is 5; retrieval first fetches a larger candidate pool and reranks it.

## Delete
`DELETE /api/v1/documents/{document_id}` with JSON body:
```json
{"organization_id":"org-1","wiki_id":"wiki-main","document_version":null}
```
Omit version to remove all embeddings for the document. This is an async job and returns 202.

## Callback
On successful indexing the configured Backend callback receives:
```json
{
  "event":"document-linking-indexed",
  "status":"succeeded",
  "job_id":"...",
  "document_id":"59",
  "result":{
    "collection":"wiki_chunks_v1",
    "chunk_count":12,
    "deleted_stale_points":8,
    "audit_outputs":["..."]
  }
}
```
A callback delivery failure does not roll back an already successful Qdrant index operation; job state exposes `callback_delivered` and `callback_error`.

# Backend ↔ Stage 3 integration guide

## Responsibility split

Backend owns `Document.id`, `WikiDocument.version_number`, editable content, publishing, immutable snapshot creation, and explicit delete calls. Stage 3 owns artifact verification, parsing, chunk identity, embeddings, Qdrant points, active search state, jobs, audits, retrieval/reranking, and terminal callbacks.

Backend does not send `WikiDocument.id` in the index request (it remains top-level `id` metadata inside the snapshot). Backend must not send chunks, chunk hashes, point IDs, dense/sparse vectors, MinIO credentials/endpoints, callback destinations, model settings, Redis/Qdrant configuration, or service credentials.

## Publication handoff

```text
Stage 1 -> Stage 2 -> Backend editable wiki document
-> user edits -> user publishes version N
-> Backend writes immutable documents/{document_id}/versions/vN/document.json
-> Backend POSTs /api/v1/documents/index-ready
```

Backend must preserve every published snapshot. Stage 3 only reads `document.json` and writes audit data under `Linking/`.

Backend identity is `(document_id, document_version)`. Stage 3 privately tracks `(document_version, index_revision)`, with `index_revision=job_id`, so a same-version re-index can be built and verified without disturbing the searchable revision. Backend does not supply or interpret this internal value.

## Index request

```http
POST /api/v1/documents/index-ready
X-API-Key: <DOC_LINKER_API_AUTH_TOKEN>
Content-Type: application/json
```

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
    "sha256": null
  },
  "document": {
    "title": "Security Policy",
    "source_filename": "security-policy.pdf",
    "language": "fa",
    "tags": ["policy"]
  },
  "replace_existing": true
}
```

The response includes job/document/version correlation. Backend can poll `GET /api/v1/jobs/{job_id}` or consume callbacks. A duplicate request can return the prior queued/running/successful job.

## Active version behavior

- First verified version becomes active.
- A verified candidate becomes active only after revision-scoped point-count verification.
- Previous versions become inactive and remain stored.
- A failed new version or same-version shadow revision never deactivates the current active revision.
- A delayed older worker compares numeric versions and cannot reactivate itself over a newer active version.
- `replace_existing=false` indexes a candidate as inactive if another active version exists.
- After a switch, Stage 3 verifies that exactly one complete `(document_version, index_revision)` is active and restores the prior tuple if validation fails.

Backend callback ordering is therefore not responsible for protecting Qdrant search state; Stage 3 enforces the guard before callback delivery.

## Published `document.json` schema

The confirmed production shape is:

```json
{
  "id": 58,
  "title": "یک سند تستی",
  "content": {
    "sections": {
      "جایگاه اول": "متن بخش اول",
      "جایگاه سوم": "متن بخش بعدی"
    },
    "source_document_id": 270
  },
  "position": "جایگاه اول",
  "owners": [1, 2],
  "status": "pending_publication",
  "version_number": 1,
  "source_document": 270,
  "source_version": null,
  "published_at": null,
  "available_actions": {},
  "hyperlink_rebuild": {},
  "created_at": "2026-10-05T12:10:45.703546Z",
  "updated_at": "2026-10-06T06:20:40.178653Z"
}
```

`source_document=270` and `content.source_document_id=270` are Backend
`Document.id` and both must match request `document_id="270"`.
`version_number=1` must match `document_version="1"`. Top-level `id=58` is the
internal `WikiDocument.id` and is metadata only. A non-empty snapshot title is
canonical; request title is fallback only.

`content.sections` is an ordered JSON map of section title to string body.
Stage 3 preserves its serialized order, emits no fake section ID, skips an
individually empty cleaned body, and rejects a snapshot with no usable sections.
Non-string/nested section shapes are rejected. Data-image HTML, Markdown, and
bare base64 URIs are removed before the RAG pipeline, while ordinary HTTP/HTTPS
links remain. The shared embedding/reranking passage is `content_title`, a blank
line, then body; stored and returned `text` remains body-only.

Position, owners, status, source version, publication timestamp, actions,
hyperlink-rebuild state, and timestamps remain namespaced metadata and are not
embedded. Owners remain snapshot metadata, and status/publication time
do not create a second Stage-3 publication gate. Harmless future metadata is
allowed and preserved; required identity/content fields remain strict.

## Search integration

Backend may pass document/tag/language/visibility filters on `POST /api/v1/search`. Stage 3 always applies organization/wiki and `version_state=active`, performs BGE-M3 dense+sparse RRF retrieval, reranks, and returns only active chunks.

Frontends should prefer `content_id`, `content_type`, `content_title`, and `content_order`. Optional `section_*`, pages, source blocks, confidence, and classifier values exist only for legacy points and may be null/empty.

## Explicit deletion

`DELETE /api/v1/documents/{document_id}` remains a separate Backend lifecycle command. It can remove all Qdrant points or a requested version. It does not delete Backend-owned MinIO snapshots. If Backend deletes the active indexed version, Stage 3 does not guess which historical version should reactivate; Backend must publish/index the desired active version explicitly.

## Failure handling

- `artifact-not-found` is returned before a job exists.
- Supplied SHA256 mismatch or accepted ETag drift fails worker indexing.
- Qdrant upsert or verification failure leaves the previous active version unchanged.
- Callback failure leaves a successful index active and is visible through `callback_delivered=false` and `callback_error`.

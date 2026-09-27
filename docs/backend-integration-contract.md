# Backend ↔ Stage 3 integration contract

This document defines responsibilities between the product Backend and the Document Linker. It intentionally introduces no endpoints beyond `backend-contract.md`.

## Backend owns

- user/document lifecycle and authorization;
- canonical `document_id`, organization/wiki identity and document version;
- deciding which successful Stage-2 artifact should be indexed;
- calling index-ready after Stage 2 completes;
- calling delete when a document/version is removed;
- translating authenticated-user permissions into search filters;
- displaying returned references;
- receiving optional Stage-3 callbacks.

## Document Linker owns

- verifying/downloading the supplied artifact from configured shared MinIO;
- normalization/adaptation, chunking and embedding;
- Qdrant collection/index/payload lifecycle;
- Redis/Celery jobs and retries;
- replacement safety and stale-point cleanup;
- synchronous retrieval/reranking;
- `Linking/` audit artifacts;
- callback delivery state.

## MinIO handoff

Backend sends an object reference, not bytes. Preferred handoff is `media/documents/{document_id}/Sectioning/ocr/sections.json` plus sibling `manifest.json`. `Sectioning/main/...` is also accepted when Backend explicitly chooses it. Stage 3 writes only under `documents/{document_id}/Linking/`.

## Index sequence

```text
Template Maker succeeds
 -> Backend POST index-ready
 -> Stage 3 returns 202/job_id
 -> async processing
 -> Backend may poll GET /jobs/{job_id}
 -> callback succeeded/failed when configured
```

Duplicate active/succeeded index requests are idempotent. After a successful delete, Stage 3 advances an internal document generation so the same artifact/version can be indexed again.

## Search sequence

```text
User -> Front -> Backend
Backend authenticates/authorizes user
Backend derives organization_id/wiki_id + visibility/ACL filters
Backend -> POST /api/v1/search
Stage 3 -> top referenced chunks
Backend -> Front
```

Stage 3 does not decide user ACL membership. It enforces filters supplied by Backend.

## Delete sequence

Backend sends `DELETE /api/v1/documents/{document_id}`; Stage 3 returns 202/job_id, deletes Qdrant points by payload filter, writes audit and callback. Backend should send a stable `request_id` when retrying the same delete event.

## Metadata that materially helps RAG

Required: document_id, organization_id, wiki_id, document_version, document.title. Recommended: source_filename, language, tags, visibility, acl_groups, artifact ETag/SHA256 when known, and manifest reference. `document.extra` is pass-through provenance/display metadata and should not contain large bodies or secrets because it is copied to chunk payloads.

## Failure semantics

- HTTP 4xx before job creation means contract/artifact error.
- HTTP 503 means dependency/enqueue failure.
- Worker errors may retry before final failure.
- Callback failure does not change a completed Qdrant operation into failure.
- Backend can resolve authoritative operation state through `GET /api/v1/jobs/{job_id}` while job TTL is active.

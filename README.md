# Organizational Wiki Intelligence — Document Linker

Stage 3 indexes immutable Backend-published wiki versions and serves hybrid semantic search. The application code is runtime-neutral: CPU/GPU behavior is selected only by Compose and environment configuration.

## Product flow

```text
Stage 1 -> Stage 2 -> Backend editable wiki document -> user edits -> user publishes
        -> immutable MinIO document.json -> Backend POST index-ready -> Stage 3
```

The authoritative published identity is `(Backend Document.id, WikiDocument.version_number)` and the only primary input path is:

```text
documents/{document_id}/versions/v{document_version}/document.json
```

Stage 3 never writes, mutates, or deletes objects under `documents/{document_id}/versions/`. Legacy Stage-2 JSON/DOCX/Markdown adapters remain in the codebase for migration/tests, but the public indexing contract does not route to them.

## Index lifecycle

Indexing is asynchronous and idempotent:

```text
stat + capture accepted ETag
-> download and verify the accepted ETag/SHA256
-> parse published snapshot
-> semantic chunking
-> BGE-M3 dense + learned sparse embeddings
-> upsert candidate version_state=inactive
-> remove obsolete chunks only in that same version
-> verify point count
-> numeric late-job guard
-> activate candidate and mark other active versions inactive
-> audit + callback
```

Historical points remain in Qdrant. For example, after publishing v1, v2, and v3, v1/v2 are `inactive` and preserved while v3 is `active`. A failed v3 leaves v2 active. A delayed v1/v2 worker cannot downgrade active v3. `replace_existing=true` changes active search state after verification; it never means deleting historical MinIO or Qdrant versions.

Normal search always filters `version_state=active`, then applies tenant and Backend-supplied document/tag/language/visibility/ACL filters. The RAG core remains BGE-M3 (1024 dense dimensions plus sparse lexical weights), Qdrant hybrid retrieval with RRF, and `BAAI/bge-reranker-v2-m3`.

## API

```text
POST   /api/v1/documents/index-ready
GET    /api/v1/jobs/{job_id}
POST   /api/v1/search
DELETE /api/v1/documents/{document_id}
GET    /health/live
GET    /health/ready
```

Protected endpoints accept `X-API-Key` or Bearer auth using `DOC_LINKER_API_AUTH_TOKEN`. MinIO endpoints, credentials, callbacks, model settings, Qdrant credentials, Redis URLs, and embeddings are never request-controlled.

See [Backend contract](docs/backend-contract.md), [Backend integration](docs/backend-integration-contract.md), and [architecture](docs/architecture.md).

## Stage-3-owned audit data

```text
documents/{document_id}/Linking/
  versions/v{document_version}/chunks.json
  versions/v{document_version}/index-manifest.json
  jobs/{job_id}/success.json
  jobs/{job_id}/failure.json
```

Qdrant is the live search store. Audit JSON contains no vectors.

## Run and verify

```bash
cp .env.example .env
docker network inspect wikio >/dev/null
docker compose config
docker compose up -d --build --remove-orphans
curl -fsS http://localhost:8090/health/ready
```

CPU stage defaults are `COMPOSE_PROFILES=cpu`, CPU Torch wheels, CPU embedding/reranker devices, and FP16 disabled. Production GPU uses `COMPOSE_PROFILES=gpu`, CUDA wheels/devices, and FP16 enabled. Do not enable both model-service profiles.

For an existing `wiki_chunks_v1` collection created before `version_state`, run the explicit one-time migration described in [deployment](docs/deployment.md). It is dry-run by default.

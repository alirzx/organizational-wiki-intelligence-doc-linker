# Stage 3 architecture

## End-to-end indexing

```text
Backend receives Stage-2 completion
  -> POST /api/v1/documents/index-ready
  -> validate request + live MinIO object
  -> shared Redis idempotency reservation
  -> HTTP 202
  -> Celery queue on shared Redis
  -> worker document lock in shared Redis
  -> optional Stage-2 manifest validation
  -> verified MinIO download (ETag/SHA/size + mutation check)
  -> artifact adapter
       sections.json  -> full Stage-2 provenance
       sections.docx  -> heading/text fallback
       sections.md    -> heading/text fallback, Base64 image stripping
  -> CanonicalDocument
  -> cleaning/normalization
  -> section-preserving semantic chunking
       paragraphs/sentences
       exact BGE tokenizer counts
       adjacent-unit BGE similarity
       target/max/min token bounds
       overlap
  -> BGE-M3 document encoding
       dense vectors
       learned lexical sparse vectors
  -> deterministic point IDs
  -> dedicated Qdrant upsert
  -> remove obsolete points from same version
  -> verify new version point count
  -> optionally remove older versions
  -> MinIO Linking audit artifacts
  -> Backend callback
```

## Retrieval

```text
POST /api/v1/search
  -> validate organization_id + wiki_id + optional filters
  -> BGE-M3 query encoding
  -> dedicated Qdrant
       dense prefetch
       sparse prefetch
       RRF fusion
  -> default 40 candidates
  -> BGE-reranker-v2-m3 pair scoring
  -> final default 5 results
  -> references + timings
```

Qdrant hybrid search uses the Query API with dense/sparse prefetch and RRF. The collection is shared by all wiki documents; organization/wiki/document identity live in payload fields and payload indexes.

## Canonical source precedence

1. `Sectioning/.../sections.json` — preferred; contains full Stage-2 text plus section/page/source-block/classifier provenance.
2. `Sectioning/.../sections.docx` — compatibility fallback; full rendered text plus embedded images, which Stage 3 excludes from the text vector index.
3. `Sectioning/.../sections.md` — compatibility fallback; full rendered text plus Base64 images, which Stage 3 strips before text processing.

JSON avoids reverse-engineering structure already produced by Stage 2. DOCX and Markdown cannot recover all page/source-block/classifier provenance and therefore return weaker references.

## Replacement and idempotency

Point IDs are deterministic from tenant, document/version, section, chunk index and content hash. The worker writes a new revision first, removes same-version stale chunks, verifies the expected count, and only then removes older versions when requested.

API job identity also includes document generation, pipeline version, chunker version, embedding model and artifact fingerprint. A successful delete increments the document generation in shared Redis. This permits safe re-indexing of the same artifact/version after deletion while keeping duplicate Stage-2 callbacks idempotent.

## Model ownership

The root Compose file has two mutually exclusive profiles: `model-service-cpu` and `model-service-gpu`. Only one should run. Both use the network alias `model-service`. API and worker call that alias over the internal `wikio` network. The one-shot `model-cache` service uses Hugging Face `snapshot_download` to populate the persistent cache without loading model weights. The selected model-service then warms BGE-M3 and the reranker exactly once.

## Storage/service ownership

```text
Shared MinIO media/documents/{id}/Sectioning/...  read-only to Stage 3
Shared MinIO media/documents/{id}/Linking/...     Stage 3 audit outputs
Shared Redis on wikio                              broker/results + jobs/locks/idempotency/generation
Dedicated Qdrant wiki_chunks_v1                    live retrieval index
```

Redis is external infrastructure reused by multiple Wiki Hami services. Stage 3 uses separate Redis logical DBs but does not own a Redis container. Qdrant is Stage-3-owned, persistent and shared concurrently by the API/search path and Celery indexing/deletion path.

## Security boundaries

- MinIO endpoint and Backend callback URL come only from deployment configuration.
- Request artifact `uri`, when present, must exactly equal `s3://{bucket}/{object_key}`.
- Artifact key must be under `documents/{document_id}/Sectioning/` and be one of `sections.json`, `sections.docx`, or `sections.md`.
- `organization_id` and `wiki_id` are mandatory on every search.
- ACL/visibility filters are supported, but Backend remains responsible for translating authenticated-user permissions into search filters.

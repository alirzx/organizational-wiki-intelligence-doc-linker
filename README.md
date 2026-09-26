# Organizational Wiki Intelligence — Document Linker

Stage 3 of Wiki Hami / Organizational Wiki Intelligence. This service turns completed Stage-2 document artifacts into a continuously growing, reference-preserving retrieval index and exposes semantic search over the whole organizational wiki.

## Position in the product

```text
Backend upload
  -> Stage 1: Doc Scanner
     OCR.txt + layout.json + visual crops
  -> Stage 2: Template Maker
     Sectioning/ocr/sections.json + sections.docx + sections.md + manifest.json
  -> Stage 3: Doc Linker (this repository)
     canonicalize -> chunk -> BGE-M3 -> Qdrant -> BGE reranker -> top referenced chunks
```

The canonical Stage-3 input is `Sectioning/ocr/sections.json`. `sections.docx` and `sections.md` are supported compatibility inputs. JSON is preferred because it already contains Stage-2 section identity, page ranges, source blocks, classifier provenance and confidence. The Markdown adapter removes inline Base64 image payloads before chunking; DOCX images are intentionally not indexed by this text RAG pipeline.

## Main capabilities

- FastAPI Backend contract with dual `X-API-Key` / Bearer authentication.
- Async index and delete operations using Redis + Celery.
- Shared MinIO acquisition with ETag/SHA256 checks and mutation detection.
- `sections.json`, DOCX and Markdown artifact adapters into one `CanonicalDocument`.
- Section-preserving semantic chunking with hard token bounds and overlap.
- `BAAI/bge-m3` dense + sparse lexical embeddings.
- Qdrant named dense/sparse vectors and payload indexes.
- Hybrid dense+sparse candidate retrieval using Qdrant RRF.
- `BAAI/bge-reranker-v2-m3` cross-encoder reranking.
- Incremental add/merge, safe replace/reindex, and filter-based deletion.
- Deterministic point IDs for idempotent retries.
- Full result provenance: document, version, section, page range, source blocks, artifact and chunk.
- MinIO audit artifacts under `documents/{id}/Linking/`.
- Retrieval evaluation script for Recall, Precision, HitRate, MRR and nDCG.

## API

### `POST /api/v1/documents/index-ready`

Backend calls this after Stage 2 succeeds. Returns `202` immediately and schedules indexing.

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
    "object_key": "documents/59/Sectioning/ocr/manifest.json"
  },
  "document": {
    "title": "Example document",
    "source_filename": "example.pdf",
    "language": "fa",
    "tags": ["policy"],
    "visibility": "internal",
    "acl_groups": ["security-team"],
    "extra": {}
  },
  "replace_existing": true
}
```

### `GET /api/v1/jobs/{job_id}`

Statuses include `queued`, `downloading`, `parsing`, `chunking`, `embedding`, `indexing`, `verifying`, `retrying`, `succeeded`, and `failed`.

### `POST /api/v1/search`

```json
{
  "query": "مسئولیت‌های تیم امنیت چیست؟",
  "organization_id": "org-1",
  "wiki_id": "wiki-main",
  "limit": 5,
  "mode": "hybrid",
  "filters": {
    "tags": ["policy"],
    "acl_groups": ["security-team"]
  }
}
```

The service retrieves a larger candidate set from Qdrant, reranks it, and returns the final chunks with references including document/version, section identity and classifier confidence, inherited page range/source blocks, artifact location, and Backend document metadata.

### `DELETE /api/v1/documents/{document_id}`

JSON body:

```json
{"organization_id":"org-1","wiki_id":"wiki-main","document_version":null}
```

A null version removes all indexed versions for that document. Deletion is asynchronous and returns `202`.

## Qdrant payload

Every point carries searchable/filterable metadata such as:

```text
organization_id, wiki_id, document_id, document_version,
document_title, source_filename, language, tags, visibility, acl_groups,
artifact_key, artifact_sha256, artifact_etag,
section_id, section_type_id, section_title, section_order,
page_start, page_end, source_blocks, section_confidence, section_classifier,
chunk_index, content_hash, token_count, document_metadata,
embedding_model, chunker_version, indexed_at
```

New documents simply upsert more points into `wiki_chunks_v1`; there is no separate vector-file merge step. Replacement writes the new revision first, verifies it, then removes obsolete chunks and older revisions. Deletion uses Qdrant payload filters.

## MinIO audit layout

```text
media/documents/{document_id}/
  Sectioning/ocr/...       # Stage 2, read-only to this service
  Linking/
    chunks.json
    index-manifest.json
    jobs/{job_id}/success.json
    jobs/{job_id}/failure.json
```

Qdrant is the live retrieval store. MinIO audit artifacts exist for traceability/reproducibility and contain chunk text/metadata, not model vectors. Audit publication is best-effort after a verified index operation and can be disabled with `DOC_LINKER_WRITE_AUDIT_ARTIFACTS=false`; an audit upload failure does not delete a valid Qdrant index.

## Models and cache

Defaults:

```text
Embedding: BAAI/bge-m3
Reranker:  BAAI/bge-reranker-v2-m3
Dense vector size: 1024
```

The Compose stack uses a one-shot `model-cache` service to download/validate BGE-M3 and the reranker into a shared Hugging Face cache, then starts one private `model-service` process that owns both models in memory. API and worker call that internal service, so model weights are not duplicated across long-running processes. API/worker load only the lightweight BGE tokenizer from the shared cache for exact chunk token counts.

Defaults are CPU-safe. For production GPU, pin a PyTorch/CUDA combination that matches the server and then set the device/FP16 environment variables. Do not randomly install the newest CUDA wheel into production and hope the dependency gods are feeling charitable.

## Local/deployment start

```bash
cp .env.example .env
# edit MinIO credentials/auth/callback

docker network inspect wikio >/dev/null 2>&1 || docker network create wikio
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8090/health/ready
```

Development without loading BGE models:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export DOC_LINKER_EMBEDDING_BACKEND=mock
export DOC_LINKER_RERANKER_BACKEND=mock
python -m pytest -q
```

Production model dependencies:

```bash
python -m pip install -r requirements-models.txt
python scripts/prefetch_models.py
```

See `docs/backend-contract.md`, `docs/architecture.md`, and `docs/deployment.md` for the detailed contracts.

## Evaluation

Build JSONL labels in the form shown by `docs/eval-set.example.jsonl`, then:

```bash
python scripts/evaluate_retrieval.py docs/eval-set.example.jsonl \
  --url http://localhost:8090/api/v1/search \
  --token "$DOC_LINKER_API_AUTH_TOKEN"
```

The script reports Recall@5/10/20, Precision@5/10/20, HitRate@5/10/20, nDCG@5/10/20, and MRR.

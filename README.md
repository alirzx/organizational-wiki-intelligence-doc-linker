# Organizational Wiki Intelligence — Document Linker

Stage 3 of Wiki Hami / Organizational Wiki Intelligence. It converts completed Stage-2 artifacts into a growing, reference-preserving Qdrant index and exposes semantic search over the organizational wiki.

## Product flow

```text
Backend upload
  -> Stage 1: Doc Scanner
  -> Stage 2: Template Maker
     Sectioning/{main|ocr}/sections.json + sections.docx + sections.md + manifest.json
  -> Stage 3: Doc Linker
     normalize -> section-aware semantic chunking -> BGE-M3
     -> Qdrant dense+sparse index -> hybrid RRF retrieval
     -> BGE reranker -> referenced top chunks
```

`sections.json` is preferred because it contains the full Stage-2 text while preserving section identity, page ranges, source blocks and classifier provenance. `sections.docx` and `sections.md` are compatibility inputs. Markdown Base64 images and DOCX images are intentionally excluded from the text vector index.

## Runtime workflow

Indexing is asynchronous: Backend calls `POST /api/v1/documents/index-ready`; API validates the shared-MinIO object, reserves an idempotent job in the shared Wiki Hami Redis and returns `202`; Celery uses that same shared Redis service as broker, validates/downloads the artifact, creates a `CanonicalDocument`, performs section-preserving semantic chunking, embeds with `BAAI/bge-m3`, and upserts dense + learned sparse vectors into the dedicated Stage-3 Qdrant service. The new version is verified before older versions are removed. Optional audit artifacts are written to `documents/{id}/Linking/` and Backend callback is sent.

Search is synchronous:

```text
query -> BGE-M3 dense+sparse -> Qdrant prefetch + RRF
      -> candidate pool (default 40)
      -> BAAI/bge-reranker-v2-m3
      -> final top 5 by default + document/section/page/source-block references
```

Delete is asynchronous and removes a whole document or one version using Qdrant payload filters. Successful deletion advances an internal generation in shared Redis so the exact same artifact/version can later be indexed again instead of being incorrectly deduplicated against an old job.

## API

```text
POST   /api/v1/documents/index-ready
GET    /api/v1/jobs/{job_id}
POST   /api/v1/search
DELETE /api/v1/documents/{document_id}
GET    /health/live
GET    /health/ready
```

Protected endpoints accept `X-API-Key` or Bearer auth using `DOC_LINKER_API_AUTH_TOKEN`. MinIO endpoint and callback URL are deployment configuration, never request-provided network destinations.

See `docs/backend-contract.md` for exact schemas and `docs/backend-integration-contract.md` for Backend/AI responsibilities.

## Redis and Qdrant ownership

Redis follows the same production pattern as Doc Scanner: Stage 3 does not launch another Redis container. It connects through the external `wikio` network to the shared service at `redis:6379`. The example allocation keeps Celery broker, Celery results and Stage-3 durable state in separate logical DBs (`3`, `4`, `5`). DevOps may choose different unused DB numbers in `.env`.

Qdrant is intentionally Stage-3-owned. `doc-linker-qdrant` is a dedicated persistent container because both synchronous search and asynchronous indexing/deletion need one concurrent vector store. Its storage survives service recreation through the `doc_linker_qdrant` volume.

## Qdrant metadata / references

Every point carries tenant/document/version, title/source filename, language/tags/visibility/ACLs, artifact key/hash/ETag, Stage-2 job/pipeline, section ID/type/title/order/classifier/confidence, inherited page range/source blocks, chunk index/text/hash/token count, embedding/chunker version and extra document metadata. New documents simply add points to one collection (`wiki_chunks_v1`); there is no vector-file merge step.

## Models and cache

```text
Embedding: BAAI/bge-m3
Reranker:  BAAI/bge-reranker-v2-m3
Dense vector dimension: 1024
Sparse representation: BGE-M3 lexical weights
```

A one-shot `model-cache` downloads both model snapshots into the persistent Hugging Face volume. Exactly one private model service owns the weights in memory. API and worker call it over the Docker network and keep only the tokenizer locally for exact Persian/mixed-language token counting.

## One Compose file

`compose.yaml` is the deployment source of truth. It owns API, worker, model cache/service and Qdrant. Shared Redis and MinIO are external Wiki Hami services on the `wikio` network. CPU and GPU model runtimes are profiles:

```env
# CPU
COMPOSE_PROFILES=cpu
TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# GPU
COMPOSE_PROFILES=gpu
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
DOC_LINKER_EMBEDDING_DEVICE=cuda:0
DOC_LINKER_EMBEDDING_USE_FP16=true
DOC_LINKER_RERANKER_DEVICE=cuda:0
DOC_LINKER_RERANKER_USE_FP16=true
```

Use `cuda:1` for both device variables when using the second visible GPU. Do not enable both model profiles simultaneously. Both expose the same private `model-service` network alias, so API/worker configuration stays identical.

## Start

```bash
cp .env.example .env
# set real MinIO credentials, shared Redis DB allocation, service tokens, callback and cpu/gpu profile

docker network inspect wikio >/dev/null
docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
curl -fsS http://localhost:8090/health/ready
```

The existing `wikio` network must already provide the shared `redis` and `minio` services. Useful targets: `make test`, `make lint`, `make validate`, `make up-cpu`, `make up-gpu`, `make logs`, `make health`.

## MinIO ownership

```text
media/documents/{document_id}/
  Sectioning/...   # Stage 2 owns; Stage 3 reads
  Linking/         # Stage 3 owns
    chunks.json
    index-manifest.json
    jobs/{job_id}/success.json
    jobs/{job_id}/failure.json
```

Qdrant is the live search store; Linking objects are audit/reproducibility artifacts and do not store model vectors.

Detailed docs: `docs/architecture.md`, `docs/backend-contract.md`, `docs/backend-integration-contract.md`, `docs/deployment.md`, `docs/configuration.md`.

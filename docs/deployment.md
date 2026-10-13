# Deployment

## Single Compose source of truth

The repository uses one root `compose.yaml`. CPU and GPU model runtimes are mutually exclusive Compose profiles in the same file.

### CPU

```env
COMPOSE_PROFILES=cpu
TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
DOC_LINKER_EMBEDDING_DEVICE=cpu
DOC_LINKER_EMBEDDING_USE_FP16=false
DOC_LINKER_RERANKER_DEVICE=cpu
DOC_LINKER_RERANKER_USE_FP16=false
```

### GPU

```env
COMPOSE_PROFILES=gpu
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
DOC_LINKER_EMBEDDING_DEVICE=cuda:0
DOC_LINKER_EMBEDDING_USE_FP16=true
DOC_LINKER_RERANKER_DEVICE=cuda:0
DOC_LINKER_RERANKER_USE_FP16=true
```

Use `cuda:1` for both model device variables when Stage 3 should use the second GPU visible inside the container. Do not enable CPU and GPU profiles simultaneously. Docker/NVIDIA Container Toolkit must already expose the required GPU(s) on the server.

## Stage-3 owned services

```text
api                  Backend-facing FastAPI service
worker               Celery indexing/deletion worker, concurrency 1
model-cache           one-shot Hugging Face snapshot downloader
model-service-cpu     CPU BGE model owner (cpu profile)
model-service-gpu     GPU BGE model owner (gpu profile)
doc-linker-qdrant     dedicated persistent Qdrant 1.19.1
```

Stage 3 does NOT create its own Redis container. It reuses the existing Wiki Hami Redis service, following the Doc Scanner pattern.

## External prerequisites

The external Docker network `wikio` must already provide/reach:

```text
redis   shared Redis service
minio   shared Wiki Hami MinIO service
```

Example Redis allocation:

```env
DOC_LINKER_CELERY_BROKER_URL=redis://redis:6379/3
DOC_LINKER_CELERY_RESULT_BACKEND=redis://redis:6379/4
DOC_LINKER_REDIS_URL=redis://redis:6379/5
```

DB numbers are deployment allocation, not protocol. DevOps may choose different unused logical DBs. Separating broker, result backend and Stage-3 state avoids mixing Celery keys with job/idempotency/lock keys.

For local development, reuse an existing local Wiki Hami Redis if available. If none exists, a temporary external Redis can be started outside this Compose stack and attached to `wikio`, for example:

```bash
docker network inspect wikio >/dev/null 2>&1 || docker network create wikio
docker run -d --name redis --network wikio redis:7.4-alpine redis-server --appendonly yes
```

Do not run that command when a `redis` service/container already exists on the network.

## First deployment

```bash
cp .env.example .env
# edit secrets, shared MinIO/Redis addresses, model profile and callback

docker network inspect wikio >/dev/null
docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
curl -fsS http://localhost:${DOC_LINKER_API_PORT:-8090}/health/ready
```

The first start can be slow because `model-cache` downloads both model snapshots. Downloads persist in `doc_linker_hf_cache`; subsequent recreations reuse them. Model-service warmup proves the selected CPU/CUDA runtime can load both models.

## Qdrant lifecycle

Qdrant is intentionally different from Redis: it is a dedicated Stage-3 service because both API/search and Celery/indexing need one concurrent, persistent vector store. `doc-linker-qdrant` stores vectors in the `doc_linker_qdrant` named volume and stays private on `wikio`; its port is not published to the host by default.

The Qdrant collection (`wiki_chunks_v1` by default) and payload indexes are created/validated by the application. Candidates are upserted with an internal `index_revision` and `version_state=inactive`; after revision-scoped verification, ordered payload updates switch active state and a post-condition check proves there is exactly one complete active revision. Historical published-version points remain stored. Only the explicit Backend delete operation removes a published version.

### One-time `version_state` migration

Existing points created before this lifecycle may lack `version_state` and `index_revision`, so they will not appear in normal active-only search until migrated. The migration never creates a missing collection and never guesses that the highest numeric version is valid. It preserves a valid sole active revision, safely activates a document with exactly one valid published version, and reports multiple versions without trustworthy active state as `ambiguous-multiple-versions`. Ambiguous documents require an operator map. Missing legacy revisions are assigned deterministic values such as `legacy-v2`; existing valid revisions are preserved.

```bash
# read-only report (default)
docker compose run --rm api python -m scripts.migrate_active_versions

# apply after reviewing the report
docker compose run --rm api python -m scripts.migrate_active_versions --apply

# resolve ambiguous documents with an explicit reviewed map
docker compose run --rm api python -m scripts.migrate_active_versions \
  --active-map /config/active_versions.json --apply
```

The mapping file is a JSON array and uses Backend `Document.id`, never `WikiDocument.id`:

```json
[
  {
    "organization_id": "org-1",
    "wiki_id": "wiki-main",
    "document_id": "64",
    "document_version": "2"
  }
]
```

Every field is required; extra fields, duplicate document identities, missing documents, non-positive versions, and versions absent from Qdrant are rejected. Dry-run is the default and performs no payload writes. Its per-document report includes tenant/wiki/document identity, discovered versions, current active state, proposed active version/revision, status, reason, whether an explicit version is required, and whether anything was applied. `multiple-active-corruption` is reported without automatically selecting a winner. The migration is intentionally not run at application startup.

## Persistent state

Stage-3-owned named volumes:

```text
doc_linker_hf_cache   downloaded model snapshots
doc_linker_qdrant     vector database storage
```

Redis persistence belongs to the shared infrastructure and is not removed by `docker compose down` in this repository. `docker compose down -v` deletes the Stage-3 Qdrant/model-cache volumes and must not be used in production unless that data loss is intentional.

## CI/CD and branch runtime

The `stage` branch deploys the CPU profile for integration testing. The `master` branch deploys the same application source with CUDA/GPU environment values. Runtime differences belong only in `.env`, Compose profile, Torch wheel source, device/FP16 variables, and CI-provided configuration.

The repository includes `.gitlab-ci.yml`; deployments must supply the environment file, preserve the external `wikio` services, validate `docker compose config`, and use fast-forward-only pulls where the deployment environment permits.

## Published schema rollout requirement

The authoritative Backend `document.json` serializer/fixture is not present in this checkout. Before production rollout, exchange a real immutable snapshot fixture with Backend and narrow/confirm `PublishedDocumentAdapter`. Stage 3 deliberately does not manufacture page, OCR, paragraph, classifier, or Template Maker provenance that is absent from the published file.

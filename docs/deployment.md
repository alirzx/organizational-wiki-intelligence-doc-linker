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

The Qdrant collection (`wiki_chunks_v1` by default) and payload indexes are created/validated by the application. New documents are upserted into the same collection. Replacement verifies the new version before deleting older version points, and document deletion removes points by payload filter.

## Persistent state

Stage-3-owned named volumes:

```text
doc_linker_hf_cache   downloaded model snapshots
doc_linker_qdrant     vector database storage
```

Redis persistence belongs to the shared infrastructure and is not removed by `docker compose down` in this repository. `docker compose down -v` deletes the Stage-3 Qdrant/model-cache volumes and must not be used in production unless that data loss is intentional.

## CI/CD ownership

No `.gitlab-ci.yml` is kept in this repository. The DevOps team owns the GitLab deployment pipeline and should provide the production `.env`, ensure the shared `wikio` dependencies exist, then run the root `compose.yaml`.

## Stage-2 provenance limitation

`sections.json` carries section-level page ranges and source blocks. Stage 3 therefore returns those inherited ranges for each chunk. Exact character-level page references require future Stage-2 enrichment or an explicit layout cross-reference.

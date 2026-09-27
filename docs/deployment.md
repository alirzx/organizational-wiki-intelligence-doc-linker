# Deployment

## Single Compose source of truth

The repository intentionally uses one root `compose.yaml`. The old production/GPU overlay files were removed because they duplicated service definitions and could drift from each other. CPU and GPU model runtimes are now mutually exclusive Compose profiles in the same file.

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
TORCH_INDEX_URL=<approved PyTorch CUDA wheel index matching the server>
DOC_LINKER_EMBEDDING_DEVICE=cuda:0
DOC_LINKER_EMBEDDING_USE_FP16=true
DOC_LINKER_RERANKER_DEVICE=cuda:0
DOC_LINKER_RERANKER_USE_FP16=true
```

Do not enable both profiles simultaneously. The GPU service requests `gpus: all`; Docker/NVIDIA Container Toolkit must already be working on the server.

## Services

```text
api                  public Backend-facing FastAPI service
worker               Celery indexing/deletion worker, concurrency 1
model-cache           one-shot Hugging Face snapshot downloader
model-service-cpu     CPU BGE model owner (cpu profile)
model-service-gpu     GPU BGE model owner (gpu profile)
doc-linker-redis      persistent Redis
doc-linker-qdrant     persistent Qdrant 1.19.1
```

Only the API port is published. Redis, Qdrant and model-service remain private on the external `wikio` network.

## First deployment

```bash
cp .env.example .env
# edit secrets and environment-specific addresses

docker network inspect wikio >/dev/null 2>&1 || docker network create wikio
docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
curl -fsS http://localhost:${DOC_LINKER_API_PORT:-8090}/health/ready
```

The first start can be slow because `model-cache` downloads both model snapshots. Downloads persist in `doc_linker_hf_cache`; subsequent recreations reuse them. Model-service warmup is the step that proves the selected CPU/CUDA runtime can actually load both models.

## GitLab CI/CD

`.gitlab-ci.yml` has `unit-test` and `deploy`. Deploy copies the GitLab file variable `ENV_FILE` to the server, fast-forwards `master`, validates Compose, rebuilds/recreates the stack and prints status.

Required GitLab variables: `SSH_PRIVATE_KEY`, `SSH_PORT`, `VM_IPADDRESS`, `SSH_USER`, `ENV_FILE`.

Default deployment path: `/opt/apps/organizational-wiki/ai-doc-linker`. Override `DOC_LINKER_DEPLOY_PATH` if needed.

The production `.env` is the source of truth for `COMPOSE_PROFILES` and `TORCH_INDEX_URL`, so the same CI job can deploy CPU or GPU without separate Compose files.

## Persistent state

Named volumes: `doc_linker_hf_cache`, `doc_linker_qdrant`, `doc_linker_redis`. Do not use `docker compose down -v` in production unless intentionally deleting vectors, job state and model cache.

## Stage-2 provenance limitation

`sections.json` currently carries section-level page ranges and source blocks. Stage 3 therefore returns those inherited ranges for each chunk. Exact character-level page references require future Stage-2 enrichment or an explicit layout cross-reference.

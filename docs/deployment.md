# Deployment

Prerequisites: Docker Compose, external network `wikio`, shared product MinIO reachable from that network, and enough RAM/VRAM for one BGE-M3 + reranker model-service process.

```bash
cp .env.example .env
# set real MinIO credentials, API token, callback, and device settings
docker network inspect wikio >/dev/null || docker network create wikio
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8090/health/ready
```

For CUDA, build the image with a PyTorch wheel index compatible with the server CUDA runtime, then apply `deployment/compose.gpu.yaml`. The default Dockerfile intentionally installs CPU PyTorch. Example shape (replace the index with the server-approved CUDA build): `TORCH_INDEX_URL=<approved-pytorch-cuda-index> docker compose build`, then `docker compose -f compose.yaml -f deployment/compose.gpu.yaml up -d`. Do not guess the CUDA wheel index in source control.

`model-cache` downloads the model files once into the persistent Hugging Face cache. `model-service` then loads BGE-M3 and the reranker once and serves the API/worker over the private Docker network. API/worker keep only the tokenizer locally for exact token-aware chunking.

Qdrant is pinned to `v1.19.1` and uses a persistent volume. Do not expose 6333 publicly. Redis is also persistent. The API is the only service intended for Backend access.

Production with externally managed Redis/Qdrant can use `deployment/compose.prod.yaml` and point environment URLs at those shared services.


## Stage-2 provenance limitation
`sections.json` is preferred because it preserves section identity, page range and source-block references. Those page values are section-level in the current Stage-2 contract. Stage 3 therefore returns the inherited section page range for each chunk; exact character-level page citations require a future Stage-2 enrichment or an optional `layout.json` cross-reference. DOCX/Markdown fallbacks cannot recover the same provenance.

# Stage 3 architecture

```text
Backend Stage-2 completion
  -> POST /documents/index-ready
  -> Redis durable job + Celery
  -> worker downloads Stage-2 artifact from shared MinIO
  -> adapter (sections.json preferred, DOCX/Markdown fallback)
  -> CanonicalDocument
  -> section-preserving semantic chunker
  -> BGE-M3 dense + lexical sparse vectors
  -> Qdrant wiki_chunks_v1
  -> MinIO documents/{id}/Linking audit artifacts
  -> Backend callback

User query
  -> POST /search
  -> BGE-M3 query dense+sparse
  -> Qdrant RRF hybrid candidate retrieval
  -> BGE-reranker-v2-m3 cross-encoder
  -> top N chunks with document/section/page/source-block references
```

## Canonical source precedence
1. `Sectioning/ocr/sections.json`
2. `Sectioning/ocr/sections.docx`
3. `Sectioning/ocr/sections.md`

JSON preserves Stage-2 provenance and avoids parsing embedded Base64 images. DOCX/Markdown are compatibility adapters and cannot recover all Stage-2 page/source-block metadata.

## Incremental wiki behavior
Every document shares one Qdrant collection. New documents are upserted into that collection; there is no vector-file merge operation. Tenant and document identity are payload fields and indexed filters.

## Replacement safety
New-version points are written and verified first. Obsolete chunks from the same version are removed, then older document versions are removed when `replace_existing=true`. This preserves the old searchable document if embedding/upsert fails before the new version is ready.

## Model service and cache
Production Compose uses one private `model-service` process as the sole owner of BGE-M3 and the reranker in memory. API and Celery worker call it over the internal Docker network. A one-shot `model-cache` service prefetches both models into the shared Hugging Face volume before `model-service` starts. API/worker load only the BGE tokenizer locally for exact chunk token accounting, so Persian and mixed-language chunks are not sized with character-count heuristics.

Direct `bge` mode is still available for local development or single-process deployments, but it should not be used simultaneously in API and worker on a constrained GPU.

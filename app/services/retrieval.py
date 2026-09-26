from __future__ import annotations

import time

from app.api.schemas import SearchRequest, SearchResponse, SearchResult
from app.core.config import Settings
from app.models.embedding import EmbeddingService
from app.models.reranker import Reranker
from app.vector.qdrant_store import QdrantStore


class RetrievalService:
    def __init__(self, settings: Settings, embedder: EmbeddingService, reranker: Reranker, vector: QdrantStore):
        self.settings = settings
        self.embedder = embedder
        self.reranker = reranker
        self.vector = vector

    def search(self, request: SearchRequest) -> SearchResponse:
        total_started = time.perf_counter()
        limit = min(request.limit or self.settings.search_default_limit, self.settings.search_max_limit)
        candidate_limit = max(limit, request.candidate_limit or self.settings.retrieval_candidate_limit)
        mode = request.mode or self.settings.retrieval_mode

        phase = time.perf_counter()
        dense, sparse_i, sparse_v = self.embedder.encode_query(request.query)
        embedding_seconds = time.perf_counter() - phase

        phase = time.perf_counter()
        self.vector.ensure_collection()
        candidates = self.vector.search(
            dense=dense, sparse_indices=sparse_i, sparse_values=sparse_v,
            organization_id=request.organization_id, wiki_id=request.wiki_id, filters=request.filters,
            limit=candidate_limit, mode=mode,
        )
        retrieval_seconds = time.perf_counter() - phase

        phase = time.perf_counter()
        passages = [str(item.payload.get("text") or "") for item in candidates]
        rerank_scores = self.reranker.score(request.query, passages) if candidates else []
        rerank_seconds = time.perf_counter() - phase

        ranked = []
        for idx, candidate in enumerate(candidates):
            rerank_score = rerank_scores[idx] if idx < len(rerank_scores) else None
            final_score = rerank_score if rerank_score is not None else candidate.score
            ranked.append((final_score, candidate, rerank_score))
        ranked.sort(key=lambda item: item[0], reverse=True)

        results = []
        for rank, (score, item, rerank_score) in enumerate(ranked[:limit], start=1):
            p = item.payload
            results.append(SearchResult(
                rank=rank, score=float(score), retrieval_score=item.score, rerank_score=rerank_score,
                text=str(p.get("text") or ""), document_id=str(p.get("document_id") or ""),
                document_version=str(p.get("document_version") or ""), document_title=str(p.get("document_title") or ""),
                source_filename=p.get("source_filename"), section_id=str(p.get("section_id") or ""),
                section_type_id=p.get("section_type_id"), section_title=str(p.get("section_title") or ""),
                section_order=int(p.get("section_order") or 0),
                section_confidence=p.get("section_confidence"),
                section_classifier=p.get("section_classifier"),
                page_start=p.get("page_start"), page_end=p.get("page_end"),
                source_blocks=list(p.get("source_blocks") or []), chunk_index=int(p.get("chunk_index") or 0),
                artifact_bucket=str(p.get("artifact_bucket") or ""),
                artifact_key=str(p.get("artifact_key") or ""), content_hash=str(p.get("content_hash") or ""),
                document_metadata=dict(p.get("document_metadata") or {}),
            ))
        return SearchResponse(
            query=request.query, mode=mode, candidate_count=len(candidates), results=results,
            timings={
                "embedding_seconds": round(embedding_seconds, 6),
                "retrieval_seconds": round(retrieval_seconds, 6),
                "rerank_seconds": round(rerank_seconds, 6),
                "total_seconds": round(time.perf_counter() - total_started, 6),
            },
        )

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.embedding import BGEEmbeddingService
from app.models.reranker import BGEReranker

logger = logging.getLogger(__name__)
settings = get_settings()
_ready = False


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=256)
    mode: Literal["documents", "query"] = "documents"


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    passages: list[str] = Field(min_length=1, max_length=500)


def authenticate(authorization: str | None = Header(default=None)) -> None:
    token = settings.model_service_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid internal model-service token")


@lru_cache(maxsize=1)
def embedder() -> BGEEmbeddingService:
    return BGEEmbeddingService(settings)


@lru_cache(maxsize=1)
def reranker() -> BGEReranker:
    return BGEReranker(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _ready
    if settings.model_service_warmup:
        logger.info("Warming Stage-3 embedding and reranking models")
        embedder().encode_documents(["Wiki Hami model service warmup."])
        reranker().score("warmup", ["warmup passage"])
    _ready = True
    yield
    _ready = False


app = FastAPI(title="Wiki Hami Doc Linker Model Service", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
def live() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
def ready(response: Response) -> dict:
    if not _ready:
        response.status_code = 503
    return {"status": "ready" if _ready else "not-ready"}


@app.post("/internal/embed", dependencies=[Depends(authenticate)])
def embed(request: EmbedRequest) -> dict:
    service = embedder()
    batch = (
        service._encode(request.texts, query=True)
        if request.mode == "query"
        else service.encode_documents(request.texts)
    )
    return {
        "dense": batch.dense,
        "sparse_indices": batch.sparse_indices,
        "sparse_values": batch.sparse_values,
    }


@app.post("/internal/rerank", dependencies=[Depends(authenticate)])
def rerank(request: RerankRequest) -> dict:
    return {"scores": reranker().score(request.query, request.passages)}

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from app import model_server
from app.api import routes
from app.models.embedding import EmbeddingBatch, HttpEmbeddingService
from scripts import prefetch_models


def test_prefetch_selects_both_models_and_shared_cache(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        embedding_model="BAAI/bge-m3",
        reranker_backend="bge",
        reranker_model="BAAI/bge-reranker-v2-m3",
    )
    calls = []
    monkeypatch.setattr(prefetch_models, "get_settings", lambda: settings)
    monkeypatch.setattr(
        prefetch_models, "_download", lambda model_id, cache_dir: calls.append(
            (model_id, cache_dir)
        )
    )
    monkeypatch.setenv("HF_HOME", str(tmp_path))

    prefetch_models.main()

    assert calls == [
        ("BAAI/bge-m3", str(tmp_path)),
        ("BAAI/bge-reranker-v2-m3", str(tmp_path)),
    ]


def test_model_warmup_exercises_embedding_and_reranker_before_ready(monkeypatch):
    calls = []
    embedding = SimpleNamespace(
        encode_documents=lambda texts: calls.append(("embedding", texts))
    )
    reranking = SimpleNamespace(
        score=lambda query, passages: calls.append(("reranker", query, passages))
    )
    monkeypatch.setattr(
        model_server, "settings", SimpleNamespace(model_service_warmup=True)
    )
    monkeypatch.setattr(model_server, "embedder", lambda: embedding)
    monkeypatch.setattr(model_server, "reranker", lambda: reranking)
    model_server._ready = False

    before = Response()
    assert model_server.ready(before) == {"status": "not-ready"}
    assert before.status_code == 503

    async def run_lifespan():
        async with model_server.lifespan(None):
            assert model_server._ready is True
            assert calls == [
                ("embedding", ["Wiki Hami model service warmup."]),
                ("reranker", "warmup", ["warmup passage"]),
            ]

    asyncio.run(run_lifespan())
    assert model_server._ready is False


def test_model_warmup_failure_never_advertises_ready(monkeypatch):
    def fail(_texts):
        raise RuntimeError("mock model load failed")

    monkeypatch.setattr(
        model_server, "settings", SimpleNamespace(model_service_warmup=True)
    )
    monkeypatch.setattr(
        model_server, "embedder", lambda: SimpleNamespace(encode_documents=fail)
    )
    model_server._ready = False

    async def run_lifespan():
        with pytest.raises(RuntimeError, match="mock model load failed"):
            async with model_server.lifespan(None):
                pass

    asyncio.run(run_lifespan())
    assert model_server._ready is False


def test_public_readiness_observes_http_model_service(monkeypatch):
    healthy = SimpleNamespace(healthcheck=lambda: True)
    monkeypatch.setattr(routes, "get_storage", lambda: healthy)
    monkeypatch.setattr(routes, "get_jobs", lambda: healthy)
    monkeypatch.setattr(routes, "get_vector", lambda: healthy)
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_backend="http",
            reranker_backend="http",
            model_service_url="http://model-service:8091",
        ),
    )
    monkeypatch.setattr(
        routes.httpx, "get", lambda *_args, **_kwargs: SimpleNamespace(is_success=False)
    )

    response = Response()
    result = routes.ready(response)
    assert response.status_code == 503
    assert result["models"] is False
    assert result["status"] == "not-ready"


def test_http_embedding_batches_more_than_256_passages_without_models(monkeypatch):
    service = HttpEmbeddingService(SimpleNamespace())
    calls = []

    def encode(texts, mode):
        calls.append((len(texts), mode))
        return EmbeddingBatch(
            dense=[[1.0] for _ in texts],
            sparse_indices=[[1] for _ in texts],
            sparse_values=[[1.0] for _ in texts],
        )

    monkeypatch.setattr(service, "_encode", encode)
    result = service.encode_documents([f"passage-{index}" for index in range(600)])
    assert calls == [(256, "documents"), (256, "documents"), (88, "documents")]
    assert len(result.dense) == 600


def test_http_embedding_tokenizer_is_cache_only(monkeypatch, tmp_path):
    captured = {}

    class FakeTokenizer:
        @staticmethod
        def from_pretrained(model, **kwargs):
            captured.update(model=model, **kwargs)
            return object()

    transformers = ModuleType("transformers")
    transformers.AutoTokenizer = FakeTokenizer
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    service = HttpEmbeddingService(
        SimpleNamespace(embedding_model="BAAI/bge-m3")
    )

    service._get_tokenizer()
    assert captured == {
        "model": "BAAI/bge-m3",
        "cache_dir": str(tmp_path),
        "local_files_only": True,
    }


def test_model_service_auth_honors_internal_token(monkeypatch):
    monkeypatch.setattr(
        model_server, "settings", SimpleNamespace(model_service_token="internal-token")
    )
    model_server.authenticate("Bearer internal-token")
    with pytest.raises(HTTPException) as raised:
        model_server.authenticate("Bearer wrong-token")
    assert raised.value.status_code == 401

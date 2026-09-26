from __future__ import annotations

from app.core.config import get_settings
from app.models.embedding import build_embedder
from app.models.reranker import build_reranker


def main() -> None:
    settings = get_settings()
    embedder = build_embedder(settings)
    print(f"Prefetching embedding model: {settings.embedding_model}")
    embedder.encode_documents(["Wiki Hami model cache warmup."])
    # This process exists to fill the shared disk cache, not retain model memory.
    del embedder
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    if settings.reranker_backend != "none":
        reranker = build_reranker(settings)
        print(f"Prefetching reranker: {settings.reranker_model}")
        reranker.score("warmup", ["warmup passage"])
    print("Model cache ready")


if __name__ == "__main__":
    main()

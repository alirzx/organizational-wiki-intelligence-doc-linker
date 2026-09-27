from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download

from app.core.config import get_settings


def _download(model_id: str, cache_dir: str) -> None:
    print(f"Caching model: {model_id}")
    snapshot_download(repo_id=model_id, cache_dir=cache_dir)


def main() -> None:
    settings = get_settings()
    cache_dir = os.getenv("HF_HOME") or "/var/lib/doc-linker/cache/huggingface"
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    _download(settings.embedding_model, cache_dir)
    if settings.reranker_backend != "none":
        _download(settings.reranker_model, cache_dir)
    print(f"Model cache ready: {cache_dir}")


if __name__ == "__main__":
    main()

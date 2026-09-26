FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/var/lib/doc-linker/cache/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-models.txt requirements-torch.txt ./
ARG INSTALL_MODELS=true
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN if [ "$INSTALL_MODELS" = "true" ]; then \
      python -m pip install -r requirements-torch.txt --index-url "$TORCH_INDEX_URL" && \
      python -m pip install -r requirements-models.txt; \
    else \
      python -m pip install -r requirements.txt; \
    fi

COPY app ./app
COPY scripts ./scripts
RUN mkdir -p /var/lib/doc-linker/cache/huggingface

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:8090/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]

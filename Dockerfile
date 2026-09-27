FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/var/lib/doc-linker/cache/huggingface \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-models.txt requirements-torch.txt ./

ARG INSTALL_MODELS=true
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.txt \
    && if [ "$INSTALL_MODELS" = "true" ]; then \
         python -m pip install -r requirements-torch.txt --index-url "$TORCH_INDEX_URL"; \
         python -m pip install -r requirements-models.txt; \
       fi

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /var/lib/doc-linker/cache/huggingface

EXPOSE 8090 8091

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]

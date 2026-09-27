PYTHON ?= python
TORCH_INDEX_URL ?= https://download.pytorch.org/whl/cpu

.PHONY: install install-dev install-models test lint validate api worker model-service \
        build up up-cpu up-gpu down ps logs health smoke prefetch-models

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

install-models: install
	$(PYTHON) -m pip install -r requirements-torch.txt --index-url $(TORCH_INDEX_URL)
	$(PYTHON) -m pip install -r requirements-models.txt

prefetch-models:
	$(PYTHON) -m scripts.prefetch_models

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check app tests scripts

validate:
	docker compose config >/dev/null

api:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8090

worker:
	celery -A app.jobs:celery_app worker --loglevel=INFO --concurrency=1 -Q $${DOC_LINKER_CELERY_QUEUE:-wiki_hami_doc_linker}

model-service:
	$(PYTHON) -m uvicorn app.model_server:app --host 0.0.0.0 --port 8091 --workers 1

build:
	docker compose build api

up:
	docker compose up -d --build --remove-orphans

up-cpu:
	COMPOSE_PROFILES=cpu docker compose up -d --build --remove-orphans

up-gpu:
	COMPOSE_PROFILES=gpu docker compose up -d --build --remove-orphans

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f api worker model-service-cpu model-service-gpu doc-linker-qdrant

health:
	curl -fsS http://localhost:$${DOC_LINKER_API_PORT:-8090}/health/ready

smoke:
	$(PYTHON) scripts/smoke_test.py

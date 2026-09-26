.PHONY: install install-dev install-models test lint api worker up down logs smoke

install:
	python -m pip install -r requirements.txt

install-dev:
	python -m pip install -r requirements-dev.txt

install-models:
	python -m pip install -r requirements-torch.txt --index-url https://download.pytorch.org/whl/cpu
	python -m pip install -r requirements-models.txt

test:
	python -m pytest -q

lint:
	python -m ruff check app tests

api:
	python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8090

worker:
	celery -A app.jobs:celery_app worker --loglevel=INFO --concurrency=1

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker qdrant redis

smoke:
	python scripts/smoke_test.py

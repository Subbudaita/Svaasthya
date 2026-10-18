# Convenience targets. Each is a one-liner you can also run by hand (see README).
.PHONY: install db migrate api web test lint

install:
	uv sync
	cd apps/web && npm ci

db:
	docker compose up -d --wait db

migrate:
	cd apps/api && uv run --env-file ../../.env alembic upgrade head

api:
	uv run --env-file .env uvicorn --factory swasthya_api.main:create_app --reload

web:
	cd apps/web && npm run dev

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .
	cd apps/web && npm run typecheck

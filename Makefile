# Convenience targets. Each is a one-liner you can also run by hand (see README).
.PHONY: install db migrate indb dev-user ask api telegram web test lint eval-vision

INDB_REPO := https://github.com/lindsayjaacks/indian-nutrient-databank-indb-
INDB_DIR := var/indb

install:
	uv sync --group data
	cd apps/web && npm ci

db:
	docker compose up -d --wait db

migrate:
	cd apps/api && uv run --env-file ../../.env alembic upgrade head

# Convert the Indian Nutrient Databank into data/nutrition/indb.json (not committed).
indb:
	@test -d $(INDB_DIR) || git clone --depth 1 $(INDB_REPO) $(INDB_DIR)
	uv run --group data python scripts/import_indb.py $(INDB_DIR)/INDB.xlsx

# Local user + Telegram link code, no Supabase needed:  make dev-user EMAIL=you@example.com
dev-user:
	@test -n "$(EMAIL)" || (echo "usage: make dev-user EMAIL=you@example.com" && exit 1)
	uv run --env-file .env python -m swasthya_api.devtools create-user --email $(EMAIL)

# Ask the coach as a user:  make ask USER=<user id> Q="why am I so tired today?"
ask:
	@test -n "$(USER_ID)$(USER)" || (echo 'usage: make ask USER_ID=<id> Q="..."' && exit 1)
	uv run --env-file .env python -m swasthya_api.devtools ask --user-id $(or $(USER_ID),$(USER)) "$(Q)"

api:
	uv run --env-file .env uvicorn --factory swasthya_api.main:create_app --reload --loop swasthya_api.aio:new_event_loop

# The worker: ritual scheduler + Telegram bot by long polling (no public URL needed).
telegram:
	uv run --env-file .env python -m swasthya_api.worker

web:
	cd apps/web && npm run dev

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .
	cd apps/web && npm run typecheck

# Spends real tokens. Needs photos + labels in evals/vision/golden/.
eval-vision:
	uv run --env-file .env python evals/vision/run.py

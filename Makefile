.DEFAULT_GOAL := help
PY := services/lumina

# The suite gets its own database.
#
# Sharing one with the dev cluster means a running worker claims a test's job out of the
# same `jobs` table and fails it — the failure is harmless, misleading, and takes an
# afternoon to trace back to the test that caused it.
TEST_DB = postgresql+asyncpg://lumina@127.0.0.1:55432/lumina_test

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install python + node dependencies
	cd $(PY) && uv sync --all-extras
	pnpm install

# Read back from the script rather than duplicated here, so there is one source of truth
# for where the local database lives.
LOCAL_DB := $(shell ./infra/local/pg.sh status >/dev/null 2>&1; echo postgresql+asyncpg://lumina@127.0.0.1:55432/lumina)

up: ## Start local postgres (no Docker needed) and apply migrations
	@./infra/local/pg.sh up
	@DATABASE_URL=$(LOCAL_DB) $(MAKE) --no-print-directory migrate

down: ## Stop local postgres
	@./infra/local/pg.sh down

db: ## Open a psql shell on the local database
	@./infra/local/pg.sh psql

db-reset: ## Delete the local cluster and rebuild it from migrations
	@./infra/local/pg.sh reset
	@$(MAKE) --no-print-directory up

up-docker: ## Start postgres, redis, minio and tusd via Docker instead
	docker compose -f infra/docker/compose.yml up -d

api: ## Run the API with reload, reachable from other machines on the network
	# --host 0.0.0.0 so the app works when the browser is not on this machine: under WSL
	# that is Windows, and it is also how you open it on a phone. Development CORS accepts
	# any origin to match; production does not.
	cd $(PY) && uv run uvicorn lumina.api.main:app --reload --host 0.0.0.0 --port 8000

worker: ## Run the light worker pool
	cd $(PY) && uv run python -m lumina.workers.light

render: ## Run the cpu-render worker pool
	cd $(PY) && uv run python -m lumina.workers.render

web: ## Run the web app, reachable from other machines on the network
	pnpm --filter @lumina/web dev --host 0.0.0.0

mock: ## Regenerate the design reference from Lumina-Web.html
	python3 apps/web/scripts/convert-prototype.py

migrate: ## Apply database migrations to both the dev and test databases
	cd $(PY) && uv run alembic upgrade head
	cd $(PY) && DATABASE_URL=$(TEST_DB) uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add scenes"
	cd $(PY) && uv run alembic revision --autogenerate -m "$(m)"

test: ## Run the python test suite against the test database
	# DATABASE_URL too, not just TEST_DATABASE_URL: any code that builds its own session —
	# a worker, a stage handler — reads DATABASE_URL, and would otherwise write into the
	# development database from inside a test run.
	cd $(PY) && TEST_DATABASE_URL=$(TEST_DB) DATABASE_URL=$(TEST_DB) uv run pytest

lint: ## Lint and type-check everything
	cd $(PY) && uv run ruff check . && uv run ruff format --check . && uv run mypy src
	pnpm -r typecheck

fmt: ## Autoformat
	cd $(PY) && uv run ruff check --fix . && uv run ruff format .

reset-demo: ## Clear the dev database to one account and a few seeded projects: make reset-demo keep=you@example.com
	cd $(PY) && uv run python scripts/reset_demo.py $(keep) --seed

fonts: ## Fetch the caption faces every registered language pack needs
	cd $(PY) && uv run python scripts/fetch_fonts.py

client: ## Regenerate the typed TS API client from OpenAPI
	pnpm client:generate

.PHONY: help install up down db db-reset up-docker api worker render web mock migrate revision test lint fmt client fonts reset-demo

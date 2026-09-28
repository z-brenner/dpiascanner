.DEFAULT_GOAL := help
UV ?= uv
PNPM ?= pnpm
PY_SRC := packages apps/api apps/worker benchmarks

.PHONY: help install lint lint-py lint-web fmt test test-py test-web run services api worker web clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install Python (uv) and web (pnpm) dependencies
	$(UV) sync --all-packages
	$(PNPM) install

lint: lint-py lint-web ## Run all linters and type checkers

lint-py:
	$(UV) run ruff check $(PY_SRC)
	$(UV) run ruff format --check $(PY_SRC)
	$(UV) run mypy packages/*/src apps/api/src apps/worker/src

lint-web:
	$(PNPM) -r run lint

fmt: ## Auto-format Python
	$(UV) run ruff format $(PY_SRC)
	$(UV) run ruff check --fix $(PY_SRC)

test: test-py test-web ## Run all tests

test-py:
	$(UV) run pytest

test-web:
	$(PNPM) -r run test

services: ## Start Postgres and Redis
	docker compose up -d --wait

api: ## Run the API server
	$(UV) run uvicorn lantern_api.main:app --reload --port 8000

worker: ## Run the worker
	$(UV) run python -m lantern_worker

web: ## Run the web dev server
	$(PNPM) --filter @lantern/web dev

run: services ## Start services, API, worker, and web together
	$(MAKE) -j3 api worker web

clean: ## Remove caches and build output
	rm -rf .mypy_cache .ruff_cache .pytest_cache apps/web/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

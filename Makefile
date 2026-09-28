.DEFAULT_GOAL := help
UV ?= uv
PNPM ?= pnpm
PY_SRC := packages apps/api apps/worker benchmarks

.PHONY: help install lint lint-py lint-web fmt test test-py test-dynamic test-docker test-web benchmark benchmark-gate run services api worker web clean

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

test-py: ## Python tests (fast; excludes dynamic and docker)
	$(UV) run pytest -m "not dynamic and not docker"

test-dynamic: ## Run the fixtures under dynamic verification against the mock server
	LANTERN_REQUIRE_DYNAMIC=1 $(UV) run pytest -m dynamic

test-docker: ## Run the Docker sandbox integration tests (builds images; needs Docker)
	LANTERN_DOCKER_TESTS=1 $(UV) run pytest -m docker

benchmark: ## Run the benchmark and regenerate benchmarks/RESULTS.md
	$(UV) run python benchmarks/run_benchmark.py --write-results

benchmark-gate: ## Fail unless canary recall is 100 percent and clean-python has no false positives
	$(UV) run python benchmarks/run_benchmark.py --check --providers stub --no-save

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

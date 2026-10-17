# ── rugbrain Makefile ──────────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/rugbrain
#
# Modes:
#   make api-dev   — start FastAPI dev server on :8012
#   make ingest    — run one-shot threat ingest into Qdrant
#   make backup    — snapshot Qdrant collection
#
# Layout:
#   src/rugbrain/   — Python package (53 modules, ~15K LOC)
#   scripts/       — operational scripts
#   Dockerfile     — multi-mode container (API default, ingest override)

SHELL := /bin/bash

.PHONY: help install dev run api-dev ingest backup restore lint format check \
        typecheck security precommit commit ci clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install Python deps (pyproject.toml + requirements.txt)
	pip install -e ".[dev]"

dev: api-dev ## Alias for starting the API server

api-dev: ## Start FastAPI dev server on :8012 with hot-reload
	uvicorn rugbrain.main:app --host 0.0.0.0 --port 8012 --reload --log-level debug

ingest: ## Run threat ingest into Qdrant (requires QDRANT_URL env)
	@test -n "$$QDRANT_URL" || echo "QDRANT_URL not set - using default http://127.0.0.1:6334"
	python scripts/ingest_threats.py

backup: ## Snapshot Qdrant collection to local snapshot dir (QDRANT_URL env)
	@test -n "$$QDRANT_URL" || (echo "QDRANT_URL not set - export it first" && exit 1)
	python scripts/backup_qdrant.py

restore: ## Restore a Qdrant snapshot (requires SNAPSHOT_FILE + QDRANT_URL)
	@test -n "$$SNAPSHOT_FILE" || (echo "SNAPSHOT_FILE not set" && exit 1)
	@test -n "$$QDRANT_URL" || (echo "QDRANT_URL not set" && exit 1)
	python scripts/backup_qdrant.py --restore "$$SNAPSHOT_FILE"

lint: ## Lint src/ and scripts/ with ruff
	ruff check src/ scripts/

format: ## Format src/ and scripts/ with ruff (writes)
	ruff format src/ scripts/
	ruff check --fix src/ scripts/

check: ## Full check: lint + format + typecheck
	ruff check src/ scripts/
	ruff format --check src/ scripts/
	mypy src/ scripts/ --ignore-missing-imports

typecheck: ## MyPy type check
	mypy src/ scripts/ --ignore-missing-imports

test: ## Run tests (placeholder - no tests yet)
	@echo "no tests yet - API endpoints covered via smoke test: make api-dev"
	exit 0

security: ## Security audit (bandit + ruff S-rules)
	bandit -r src/ scripts/ -q
	ruff check --select S src/ scripts/

precommit: ## Install pre-commit hooks
	pre-commit install

commit: ## Interactive commit with commitizen
	cz commit || git commit

ci: lint typecheck security ## Full CI: lint + typecheck + security

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache

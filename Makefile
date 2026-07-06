# ── rmi-rag Makefile ──────────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/rmi-rag
#
# Small Python utility repo: 2 standalone scripts (no app/ package).
# Ingests threats into Qdrant + backs up the Qdrant collection.
#
# Layout:
#   scripts/ingest_threats.py   - push threat intel into Qdrant
#   scripts/backup_qdrant.py    - snapshot a Qdrant collection
#   requirements.txt            - qdrant-client, sentence-transformers, torch

SHELL := /bin/bash

.PHONY: help install dev run ingest backup restore lint format check test \
        typecheck security precommit commit ci clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install Python deps (qdrant + sentence-transformers + torch CPU)
	pip install -r requirements.txt

dev: ## Alias for `make ingest` (interactive re-ingest for development)
	@echo "use:  make ingest      # run ingest_threats.py"
	@echo "      make backup      # snapshot Qdrant collection"

run: ingest ## Alias for ingest (dev-loop entry point)

ingest: ## Run threat ingest into Qdrant (requires QDRANT_URL env)
	@test -n "$$QDRANT_URL" || (echo "QDRANT_URL not set - export it first" && exit 1)
	python3 scripts/ingest_threats.py

backup: ## Snapshot Qdrant collection to local snapshot dir (QDRANT_URL env)
	@test -n "$$QDRANT_URL" || (echo "QDRANT_URL not set - export it first" && exit 1)
	python3 scripts/backup_qdrant.py

restore: ## Restore a Qdrant snapshot (requires SNAPSHOT_FILE + QDRANT_URL)
	@test -n "$$SNAPSHOT_FILE" || (echo "SNAPSHOT_FILE not set" && exit 1)
	@test -n "$$QDRANT_URL" || (echo "QDRANT_URL not set" && exit 1)
	python3 scripts/backup_qdrant.py --restore "$$SNAPSHOT_FILE"

lint: ## Lint scripts/ with ruff
	ruff check scripts/

format: ## Format scripts/ with ruff (writes)
	ruff format scripts/
	ruff check --fix scripts/

check: ## Full check: lint + format + typecheck
	ruff check scripts/
	ruff format --check scripts/
	mypy scripts/ --ignore-missing-imports

typecheck: ## MyPy type check
	mypy scripts/ --ignore-missing-imports

test: ## Run tests (placeholder - no tests yet)
	@echo "no tests yet - scripts are CLI tools; smoke test via make ingest/backup"
	exit 0

security: ## Security audit (bandit + ruff S-rules)
	bandit -r scripts/ -q
	ruff check --select S scripts/

precommit: ## Install pre-commit hooks
	pre-commit install

commit: ## Interactive commit with commitizen
	cz commit || git commit

ci: lint typecheck security ## Full CI: lint + typecheck + security

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache

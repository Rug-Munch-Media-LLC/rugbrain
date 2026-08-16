[//]: # (SPDX-License-Identifier: MIT)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# AGENTS.md — RugBrain

> AI agent contract. Read this before touching anything in this repo.

## Status
canonical · owner=crmuncher · last_updated=2026-07-06

## What This Repo Is
RAG permanence layer for Rug Munch Intelligence. Stores vector embeddings of threats, scams, labels, and forensic insights in Qdrant. Owns Qdrant collection definitions, embedding model configs, and ingestion scripts from Ponder, Pry, and APIs.

## Type
`backend` · language=Python 3.12

## Where It Lives
- Source: `https://github.com/RugMunchMedia/rugbrain`

## Built With
- Standards: See the project documentation for development standards
- Toolchain: See README.md for development toolchain
- Conventions: See CONTRIBUTING.md for coding conventions

## Components
- `collections/` — Qdrant collection definitions and configs (20 collections)
- `scripts/` — ingestion scripts (threats, scams, labels, news)
- `requirements.txt` — Python deps

## Dependencies
- Vector database (Qdrant or compatible)
- Embedding service (configurable)
- Backend service for query endpoints

> **Data placement (canonical):** knowledge → RAG, code → `rmi-codebase-mcp` — see `docs/DATA-PLACEMENT.md` before ingesting any new source.

## 🚨 GIT POLICY — NON-NEGOTIABLE

1. **NEVER commit .env files, credentials, API keys, or tokens**
2. **NEVER use HTTPS remotes with embedded passwords — SSH only**
3. **NEVER commit before gitleaks passes** (`gitleaks detect --no-git`)
4. **NEVER push to main without lefthook pre-commit passing**
5. **All secrets come from the secrets manager — NEVER hardcoded**
6. **All repos must have: .gitignore, .gitleaks.toml, .lefthook.yml**
7. **Internal IPs/hostnames use [INTERNAL] placeholder in public repos**
8. **Any agent caught leaking secrets will have their work rejected**

## 🚨 REPO-SPECIFIC RULES

1. **No nested repos.** Don't commit other complete project trees inside this one.
2. **No data blobs.** Don't commit `*.zip`, `*.parquet`, model weights, sqlite files, Qdrant snapshots.
3. **No file > 500 lines.** Split or refactor.
4. **No mainnet RPC keys in source.** Use environment variables.
5. **Update STATUS.md before committing.** Builders must know where we are.
6. **Read DECISIONS.md before architectural changes.** Don't repeat rejected designs.

## Commands
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest_threats.py      # example ingestion
```

## Architecture
See [ARCHITECTURE.md](ARCHITECTURE.md). Update it when components change.

## Status
See [PRODUCT.md](PRODUCT.md) and [ROADMAP.md](ROADMAP.md).

## Owners
- Primary: crmuncher
- Backup: TBD

## Related Repos
- `rmi-backend/` — query endpoint host
- `pryscraper/` — ingestion source
- See product documentation for available products

## Open-Core Protection

This repo is open source (MIT). The code here demonstrates RAG patterns for
crypto intelligence — it is NOT the complete RugMunch production RAG pipeline.
The managed platform at rugmunch.io includes proprietary enhancements.

See [CONTRIBUTING.md](CONTRIBUTING.md) for project methodology.
for details on what information belongs in public vs private repos.

## Git Policy

This repo:
- Classification: OPEN SOURCE
- Remotes: codeberg + github + gitlab + radicle (primary)
- Commits: type(scope): description
- Never commit: .env, secrets, internal IPs, security reports

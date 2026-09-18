[//]: # (SPDX-License-Identifier: MIT)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# AGENTS.md — RugBrain

> AI agent contract. Read this before touching anything in this repo.

## Status
canonical · owner=crmuncher · last_updated=2026-09-18

## Canonical Standards (fleet)
This repo follows the fleet-wide standards owned by the `standards` repo, not local copies.
Before cross-repo or architectural changes, load the playbook and read the canonical docs:
- Playbook: `rmi_playbook('standards')` (ContextCrypto MCP) — returns the routed brief.
- `standards/DEVELOPMENT-STANDARDS.md` — coding, testing, review gates.
- `standards/REPO-BOUNDARIES.md` — what each repo may own/depend on.
- `standards/DATA-STORES.md` — canonical data placement (knowledge → RAG, code → codebase MCP).

Do not duplicate these standards here; link to them.

## Repo Boundaries (read before importing anything)
1. **RugBrain is the RAG engine only** — retrieval, embedding, ingestion, eval. Nothing else.
2. **No rmi-backend imports** — never `import app.*`; no `app.*` stubs (deleted 2026-09-18).
3. **No rmi-backend runtime calls** — ingest/read DIRECT (local Qdrant + direct upstreams); never `localhost:8000`.
4. **Backend reaches RugBrain via HTTP** (`rugbrain_client`); this repo never reaches back.
5. **Data placement** — knowledge belongs here; code belongs in `rmi-codebase-mcp` (see DATA-STORES.md).

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
- Vector database (Qdrant or compatible) — local, written directly
- Embedding service (configurable)
- Direct upstream APIs (RSS, Polymarket, DeFiLlama, Fear & Greed) — no backend proxy

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
- `rmi-backend/` — INBOUND caller of RugBrain's HTTP API only (never a runtime dependency)
- `pryscraper/` — ingestion source
- `standards/` — canonical fleet docs (see Canonical Standards above)
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

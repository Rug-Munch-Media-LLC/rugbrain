[//]: # (SPDX-License-Identifier: MIT)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# AGENTS.md — RMI RAG

> AI agent contract. Read this before touching anything in this repo.

## Status
canonical · owner=crmuncher · last_updated=2026-07-06

## What This Repo Is
RAG permanence layer for Rug Munch Intelligence. Stores vector embeddings of threats, scams, labels, and forensic insights in Qdrant. Owns Qdrant collection definitions, embedding model configs, and ingestion scripts from Ponder, Pry, and APIs.

## Type
`backend` · language=Python 3.12

## Where It Lives
- Source: `https://git.rugmunch.io/RugMunchMedia/rmi-rag`
- Deployed: Docker on Talos (compose in fleet-infra)
- Port: 6333 (Qdrant, shared)

## Built With
- Standards: [git.rugmunch.io/RugMunchMedia/standards](https://git.rugmunch.io/RugMunchMedia/standards)
- Toolchain: [TOOLCHAIN.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/TOOLCHAIN.md)
- Conventions: [CONVENTIONS.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/CONVENTIONS.md)

## Components
- `collections/` — Qdrant collection definitions and configs (20 collections)
- `scripts/` — ingestion scripts (threats, scams, labels, news)
- `requirements.txt` — Python deps

## Dependencies
- Qdrant at `127.0.0.1:6333`
- bge-m3 embeddings at `127.0.0.1:8001/embed`
- RMI backend for query endpoints
- Postgres (source data via Ponder)

## 🚨 CRITICAL RULES

1. **No nested repos.** Don't commit other complete project trees inside this one.
2. **No secrets.** Never commit `.secrets/`, `.env`, `*.pem`, `*.key`, `*.crt`. Use gopass.
3. **No data blobs.** Don't commit `*.zip`, `*.parquet`, model weights, sqlite files, Qdrant snapshots.
4. **No file > 500 lines.** Split or refactor.
5. **No mainnet RPC keys in source.** Use gopass and environment.
6. **Update STATUS.md before committing.** Builders must know where we are.
7. **Read DECISIONS.md before architectural changes.** Don't repeat rejected designs.

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
- See [standards/PRODUCTS.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/PRODUCTS.md).
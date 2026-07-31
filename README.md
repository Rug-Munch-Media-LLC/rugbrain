# RMI RAG — Retrieval-Augmented Generation for Crypto Intelligence

RAG layer for Rug Munch Intelligence. Embeds scam patterns, news/social
intel, and wallet/entity intelligence into vectors and serves them back
through semantic, hybrid, and entity-aware search. Powers the scanner's
**Known Scam Patterns** check, the bot's `/research` and `/investigate`
commands, and the frontend's AI chat / wallet forensics.

**Status:** live service, collections being seeded (see
[ARCHITECTURE.md](ARCHITECTURE.md) §7 for honest current gaps).

## What it serves

| Domain | Content | Search entry point |
|--------|---------|--------------------|
| Scam Intelligence | Known scam patterns, historical hacks, scanner findings | `/api/v1/rag/v2/scam-check`, `/api/v1/search` |
| Market Awareness | News, CT rundown, market brief, social sentiment | rmi-backend `/api/v1/rag/v2/search` |
| Entity Intelligence | Wallet labels, entity profiles, cross-chain identities | `/api/v1/rag/investigate`, three-pillar search |

## Architecture

- **Embedding:** BGE-M3 (primary, via Ollama, 1024-dim); nomic-embed-text
  fallback (768-dim). No hash fallback — fails loud (HTTP 503).
- **Vector store:** Qdrant (Cosine, 1024-dim), collections created on demand.
- **Warm store:** Redis — documents, index sets, semantic cache, ingest queues.
- **Retrieval:** three-pillar search (dense + sparse/BM25-SPLADE + entity)
  fused with Reciprocal Rank Fusion; optional MMR dedup + cross-encoder rerank.
- **Ingestion:** scanner → Redis queues → subscriber/poller → embed → Qdrant;
  nightly news/social ingest; idempotent seed scripts.
- **Permanence:** nightly snapshots to Cloudflare R2.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full document (components,
API reference, data flows, operations, roadmap).

## Scope

- Qdrant collection definitions and payload schema
- Embedding model configs (bge-m3 primary) and chunking strategies
- Ingestion scripts (threats, scams, news, wallet labels)
- Three-pillar retrieval engine, RRF fusion, scam-check API
- R2 permanence (backup/restore)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/RugMunchMedia/rmi-rag.git
cd rmi-rag

# Install dependencies
pip install -e .

# Run the threat-intel seeder (requires Qdrant + Redis + Ollama running)
python scripts/seed_threat_intel.py

# Run the ingest one-shot (same thing, inside the container)
docker compose run --rm rag-ingest

# Start the API server (FastAPI on :8012)
python -m rmi_rag
# or: docker compose up -d rag-api
```

Requires a running Qdrant instance (`QDRANT_URL`), Redis (`REDIS_HOST`),
and Ollama with `bge-m3` pulled (`OLLAMA_URL`). See `.env.example`.

## API docs

Interactive: `http://localhost:8012/docs` once the server is up.
Consumer-facing reference: [README-RAG-API.md](README-RAG-API.md).

> ⚠️ README-RAG-API.md is partially stale — it still documents
> nomic-embed-text as primary; the canonical embedder is now bge-m3 (1024-dim).

## Repo status

| Area | Status |
|------|--------|
| API service (port 8012) | LIVE (`/health`, `/ready`, search, scam-check) |
| Collections in Qdrant | 3 exist (`threat_intel`, `rmi_agent_memory`, `rmi_community`), 0 points — seeding in progress |
| Scanner → RAG ingestion | Wired (subscriber + poller), `ingested_count: 0` — backend not publishing yet |
| `/investigate`, `/confidence` | WIP — 500 until `src/app/` packaging is restored |
| Qdrant nightly backup → R2 | ❌ failing (missing R2 S3 credentials) |
| Tests | 1 failing, coverage ~10% — see TODO.md |

Source of truth for gaps: [TODO.md](TODO.md) (audited 2026-07-31).

## Open-Core Model

This repo is open source (MIT). The code here demonstrates RAG patterns for
crypto intelligence. The RugMunch managed platform at rugmunch.io includes
proprietary enhancements.

See [OPEN-CORE.md](OPEN-CORE.md) for details.

## Standards

This repo follows Rug Munch Media standards:
https://github.com/RugMunchMedia/standards

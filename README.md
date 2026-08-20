# RugBrain — Crypto Intelligence RAG Engine

RugBrain (formerly RugSense / RMI RAG) is the RAG layer for Rug Munch
Intelligence. It embeds scam patterns, news/social intel, wallet/entity
intelligence, and **real-time crypto security incidents** into vectors and
serves them back through semantic, hybrid, and entity-aware search. Powers
the scanner's **Known Scam Patterns** check, the bot's `/research` and
`/investigate` commands, and the frontend's AI chat / wallet forensics.

**Status:** live service, 40+ Qdrant collections populated (see
[docs/COLLECTION_REGISTRY.md](docs/COLLECTION_REGISTRY.md) for the live
inventory; [TODO.md](TODO.md) for honest current gaps).

## What it serves

| Domain | Content | Search entry point |
|--------|---------|--------------------|
| Scam Intelligence | Known scam patterns, historical hacks, scanner findings | `/api/v1/rag/v2/scam-check`, `/api/v1/search` |
| Market Awareness | News, CT rundown, market brief, social sentiment | rmi-backend `/api/v1/rag/v2/search` |
| Entity Intelligence | Wallet labels, entity profiles, cross-chain identities | `/api/v1/rag/investigate`, three-pillar search |
| Security Alerts | Live exploit/rug-pull incidents (Qdrant `security_alerts`) | `src/rugbrain/alerts.py` poller → NATS/Redis + ClickHouse |

## Real-time security alert ingestion

`src/rugbrain/alerts.py` polls free security-intel sources every 5 minutes
(CoinGecko new listings, DeFiLlama hacks, Rekt News, CertiK/PeckShield/Beosin
via Nitter RSS or SearXNG) and, for each new incident:

1. embeds the incident description (Infinity qwen3-embedding-4b, 2560-dim)
2. stores it in Qdrant `security_alerts`
3. pushes to NATS `alerts.crypto.security` (Redis pub/sub fallback)
4. logs to ClickHouse `rag_security_alerts` for historical tracking

```bash
python -m rugbrain.alerts --once    # single pass (cron friendly)
python -m rugbrain.alerts           # long-running poller
```

Optional extras (graceful degradation when absent): `nats-py`,
`clickhouse-connect` (`pip install -e '.[alerts]'`).

## Market awareness

`src/rugbrain/market_awareness.py` tracks the current landscape via the free
DataBus-style APIs (CoinGecko, DeFiLlama, DexScreener, Fear & Greed) and
returns:

```json
{
  "trending_scams": [...],
  "recent_exploits": [...],
  "market_sentiment": "fearful|neutral|greedy"
}
```

`score_awareness(query)` / `boosted_risk(query)` feed this awareness into RAG
scoring for new token scans, so a token matching a recently exploited
protocol or a trending scam is immediately flagged.

## Architecture

- **Embedding:** BGE-M3 (primary, via Infinity, 2560-dim). No hash fallback —
  fails loud (HTTP 503).
- **Vector store:** Qdrant (Cosine, 2560-dim), collections created on demand.
- **Warm store:** Redis — documents, index sets, semantic cache, ingest queues.
- **Retrieval:** three-pillar search (dense + sparse/BM25-SPLADE + entity)
  fused with Reciprocal Rank Fusion; optional MMR dedup + cross-encoder rerank.
- **Ingestion:** scanner → Redis queues → subscriber/poller → embed → Qdrant;
  nightly news/social ingest; idempotent seed scripts.
- **Permanence:** nightly snapshots to Backblaze B2 (`rmi-rag-permanence`).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full document (components,
API reference, data flows, operations, roadmap).

## Scope

- Qdrant collection definitions and payload schema
- Embedding model configs (qwen3-embedding-4b primary) and chunking strategies
- Ingestion scripts (threats, scams, news, wallet labels)
- Three-pillar retrieval engine, RRF fusion, scam-check API
- B2 permanence (backup/restore)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/RugMunchMedia/rugbrain.git
cd rugbrain

# Install dependencies
pip install -e .

# Run the seeder (requires Qdrant + Redis + Infinity running)
python scripts/seed_all.py --dry-run   # preview first
python scripts/seed_all.py              # seed everything

# Run the ingest one-shot (same thing, inside the container)
docker compose run --rm rag-ingest

# Start the API server (FastAPI on :8012)
python -m rugbrain
# or: docker compose up -d rag-api
```

Requires a running Qdrant instance (`QDRANT_URL`), Redis (`REDIS_HOST`),
and Infinity with `qwen3-embedding-4b` loaded (`INFINITY_URL`). See `.env.example`.

## Adding a data source

New ingest sources follow the `Source` interface in
`src/rugbrain/ingest_sources/base.py` — see
**[docs/ADDING-A-SOURCE.md](docs/ADDING-A-SOURCE.md)** for the full
step-by-step (interface, `*_v2` collection naming, registration, tests).

## API docs

Interactive: `http://localhost:8012/docs` once the server is up.
Consumer-facing reference: [README-RAG-API.md](README-RAG-API.md).

> ⚠️ README-RAG-API.md is partially stale — it still documents
> nomic-embed-text as primary; the canonical embedder is now qwen3-embedding-4b (2560-dim).

## Repo status

| Area | Status |
|------|--------|
| API service (port 8012) | LIVE (`/health`, `/ready`, search, scam-check) |
| Collections in Qdrant | 40+ live, `*_v2` names (`threat_intel_v2`, `scam_intel_v2`, `research_v2`, …) — see [docs/COLLECTION_REGISTRY.md](docs/COLLECTION_REGISTRY.md) |
| Programmatic sources | 25+ `Source` implementations in `src/rugbrain/ingest_sources/` — see [docs/ADDING-A-SOURCE.md](docs/ADDING-A-SOURCE.md) |
| `/investigate`, `/confidence` | Routed in `src/rugbrain/endpoints.py` (`/api/v1/rag/…`) |
| Qdrant nightly backup → B2 | ✅ uploading to `rmi-rag-permanence` (b2sdk) |
| Tests | 880+ collected; run `pytest` before pushing — see [TODO.md](TODO.md) |

Source of truth for gaps: [TODO.md](TODO.md).

## Open-Core Model

This repo is open source (MIT). The code here demonstrates RAG patterns for
crypto intelligence. The RugMunch managed platform at rugmunch.io includes
proprietary enhancements.

See [OPEN-CORE.md](OPEN-CORE.md) for details.

## Standards

This repo follows Rug Munch Media standards:
https://github.com/RugMunchMedia/standards

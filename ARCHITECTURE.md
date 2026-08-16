# RugBrain — Architecture

**Status:** canonical
**Last updated:** 2026-07-31
**Owner:** crmuncher
**Repo:** [RugMunchMedia/rugbrain](https://git.rugmunch.io/RugMunchMedia/rugbrain)

> This document describes the RAG system **as it exists today** — what is live,
> what is wired but idle, and what is explicitly work-in-progress. It does not
> describe aspirational features. Where something is broken or incomplete, this
> document says so.

---

## 1. System Overview

RugBrain (formerly RMI RAG) is the retrieval-augmented generation layer for RugMunch Intelligence.
It makes crypto threat knowledge **semantically searchable**: instead of
exact-match lookups, queries are embedded into vectors and matched against a
curated corpus of scam patterns, news, and entity intelligence. Retrieved
context is served to the scanner, the Telegram bot, and the frontend AI chat
so their answers are grounded in stored evidence rather than model priors.

The system serves three intelligence domains:

### 1.1 Scam Intelligence
Semantic search over known scam patterns, hacked protocols, and exploit
techniques. Powers the scanner's *Known Scam Patterns* check and the bot's
`/research` command. Goal: immediately identify repeat deployer patterns, known
exploit signatures, and rug-pull techniques by matching against our curated
database of Web3 security incidents.

**Where it lives today:** `threat_intel` collection (Qdrant) + the curated
pattern corpus in `KNOWN_SCAM_PATTERNS` (10 patterns), `seed_all.py` (10
patterns + 8 historical hacks + 6 entity profiles), and `seed_threat_intel.py`
(13 static vulnerability patterns + 6 historical hacks + Redis seed docs).

### 1.2 Market Awareness
Real-time news and social sentiment, embedded and searchable. When a user asks
"what's happening with ETH today?", the RAG layer retrieves recent articles
(CoinTelegraph, Decrypt, The Block, crypto Twitter/X via the DataBus news
aggregator) and returns grounded, sourced answers. Powers the frontend's AI
chat and market intelligence features.

**Where it lives today:** the nightly ingest pipeline (`rag_ingestion.py`,
3 AM UTC) pulls news, CT rundown, market briefs, and social metrics into Redis
collections (`news_articles`, `ct_rundown`, `market_intel`, `social_intel`).
The firehose module (`firehose/news.py`) pulls DataBus RSS + prediction
markets. **Gap:** these collections are not yet materialized in Qdrant — see
§7.

### 1.3 Entity Intelligence
Wallet labels, deployer histories, and cross-chain entity resolution, embedded
for investigator queries. Powers the bot's `/investigate` and the frontend's
wallet forensics. Goal: "show me everything about wallet 0x..." returns labeled
entities, related addresses, known associations, and scam involvement.

**Where it lives today:** `rmi_agent_memory` collection (entity profiles +
known addresses) and the wallet-label paths in `crypto_embeddings.py`
(`embed_wallet`) and `search/filters.py` (`_embed_by_collection` →
`wallet_profiles`). Cross-chain resolution and investigation narratives exist
in `src/app/` (currently being restored — see §7).

### 1.4 High-level flow

```
        ┌──────────────────────────────────────────────────────┐
        │                        QUERIES                        │
        │  "known scam patterns for 0xdead…"                   │
        │  "what's happening with ETH today?"                  │
        │  "show me everything about wallet 0x…"               │
        └─────────────────────────┬────────────────────────────┘
                                  │
                                  ▼
        ┌──────────────────────────────────────────────────────┐
        │                  RETRIEVAL LAYER                      │
        │  rmi-backend  /api/v1/rag/v2/*  (search/ingest/stats)│
        │  rugbrain      /api/v1/rag/v2/scam-check  /api/v1/search│
        │  three-pillar: dense + sparse + entity → RRF fusion  │
        └──────────────┬───────────────────────┬───────────────┘
                       │                       │
                       ▼                       ▼
        ┌──────────────────────┐   ┌──────────────────────────┐
        │  Qdrant (vector)     │   │  Redis (doc store +      │
        │  1024-dim BGE-M3     │   │  warm numpy-cosine scan)  │
        │  Cosine distance     │   │  + semantic cache + TTLs  │
        └──────────────┬───────┘   └──────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────────────────────┐
        │                 EMBEDDING LAYER                       │
        │  BGE-M3 (primary, Infinity, 1024-dim)                 │
        │  nomic-embed-text (fallback, 768-dim, backend chain)  │
        │  legacy chain: OpenRouter → LocalBGE → HF (CryptoEmbedder)│
        └──────────────┬───────────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────────────────────┐
        │                INGESTION PIPELINE                     │
        │  Scanner → Redis queue → subscriber/poller → embed → Qdrant│
        │  News/social → nightly rag_ingestion → Redis          │
        │  Scam DBs → seed scripts (seed_all, seed_threat_intel)│
        │  Code patterns → rmi-codebase-mcp (Qdrant, external)│
        └──────────────────────────────────────────────────────┘
```

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CONSUMERS                             │
│  rmi-frontend (Chat)  │  rugmunchbot (/research)  │ Scanner │
└───────────┬──────────────┬─────────────────┬────────────────┘
            │              │                 │
     ┌──────┴──────────────┴─────────────────┴──────────────┐
     │                 RETRIEVAL LAYER                       │
     │  rmi-backend: POST /api/v1/rag/v2/search (LIVE)       │
     │              POST /api/v1/rag/v2/ingest (LIVE)        │
     │              GET  /api/v1/rag/v2/stats (LIVE)         │
     │  rugbrain:     GET  /api/v1/rag/v2/scam-check (LIVE)   │
     │              GET  /api/v1/search (LIVE)               │
     │              POST /api/v1/rag/investigate (WIP 500)   │
     │              POST /api/v1/rag/confidence (WIP 500)    │
     │  three-pillar: dense + sparse + entity + RRF + MMR    │
     └──────────────┬─────────────────────┬──────────────────┘
                    │                     │
        ┌───────────┴────────┐   ┌────────┴─────────────┐
        │  Qdrant             │   │  Redis               │
        │  (vector store)     │   │  (warm store)        │
        │  1024-dim BGE-M3    │   │  docs rag:{c}:{id}   │
        │  Cosine distance    │   │  idx rag:idx:{c}     │
        │  3 collections exist│   │  numpy cosine scan   │
        └───────────┬────────┘   └────────┬─────────────┘
                    │                     │
        ┌───────────┴─────────────────────┴──────────────┐
        │              EMBEDDING LAYER                    │
        │  PRIMARY:  bge-m3 via Infinity, 1024-dim        │
        │  FALLBACK: nomic-embed-text via Infinity, 768-dim │
        │  (legacy: OpenRouter → LocalBGE → HF)           │
        └───────────┬─────────────────────────────────────┘
                    │
        ┌───────────┴─────────────────────────────────────┐
        │              INGESTION PIPELINE                  │
        │  Scanner → Redis pub/sub rmi:rag:ingest          │
        │         → Redis list rmi:rag:findings_queue      │
        │         → subscriber + poller → embed → Qdrant   │
        │  News/social → nightly rag_ingestion (3 AM UTC)  │
        │  Scam DBs → scripts/seed_threat_intel.py         │
        │          → scripts/seed_all.py                   │
        │  Wallet labels → embed_wallet → Redis/Qdrant     │
        │  Code patterns → rmi-codebase-mcp Qdrant (ext) │
        └──────────────────────────────────────────────────┘
```

### 2.1 Runtime topology

| Component | Hostname (Docker) | Port | Purpose |
|-----------|-------------------|------|---------|
| rugbrain API | `rugbrain-api` | 8012 | FastAPI service: search, scam-check, health |
| Qdrant | `rmi-qdrant` | 6333 | Vector store (REST; auto-created collections) |
| Redis (DragonflyDB) | `rmi-redis` | 6379 | Doc store, queues, semantic cache, rate limits |
| Infinity | `infinity-rag` | 8001 | Embedding/reranking inference (BAAI/bge-m3, BAAI/bge-reranker-v2-m3) |
| rmi-backend | `rmi-backend` | 8000 | Retrieval layer `/api/v1/rag/v2/*`, scanner, DataBus |
| rmi-codebase-mcp | external | — | Code-pattern embeddings (Qdrant) |

**Qdrant connection fallback:** `QDRANT_URL` env (default `http://rmi-qdrant:6333`)
→ `http://127.0.0.1:6333` → `http://localhost:6333`. First responsive URL is
cached for the process lifetime (`qdrant_store._resolve_qdrant_url`).

---

## 3. Component Details

### 3.1 Qdrant (vector store)

**Distance:** Cosine. **Dimension:** 1024 (BGE-M3) — collections are created
on-demand by `QdrantVectorStore._ensure_collection` with the embedding's
dimension.

**Collection inventory (verified 2026-07-31):**

| Collection | Exists in Qdrant? | Points | Purpose |
|------------|-------------------|--------|---------|
| `threat_intel` | ✅ | 0 (seeding in progress) | Scam patterns, historical hacks, scanner findings |
| `rmi_agent_memory` | ✅ | 0 | Entity profiles, wallet labels, known addresses |
| `rmi_community` | ✅ | 0 | Community-reported scams, security best-practices topics |
| `scanner`, `wallet`, `news`, `research`, `scams` | ❌ configured, not created | — | Target collections per TODO (2026-07-31) |
| `scam_intel`, `deployer_history`, `wallet_labels`, `contract_audit`, `phishing_db` | ❌ | — | rmi-backend canonical set (`app/rag/models.py`) |

**Payload schema** (written by `QdrantVectorStore.insert`, shared by all
collection paths):

```json
{
  "id": "uuid-v5(doc_id)",          // deterministic — enables idempotent upserts
  "vector": [1024 floats],
  "payload": {
    "content": "≤10,000 chars of normalized text",
    "metadata": { "token_address": "...", "chain": "ethereum", "...": "..." },
    "source": "scanner | curated_patterns | historic_db | seed | ...",
    "severity": "low | medium | high | critical | informational",
    "chain": "ethereum | solana | multi | ...",
    "collection": "threat_intel"
  }
}
```

Point IDs are UUID v5 (`uuid5(NAMESPACE_DNS, doc_id)`) so re-ingesting the same
document overwrites the same point instead of duplicating it. Scanner findings
use `scanner_{chain}_{token_address}`; seed docs use `seed:<id>` /
`pattern-<name>` / `hack-<name>` / `actor-<name>`.

### 3.2 Infinity / BGE-M3 (embedding + reranking)

| Setting | Value |
|---------|-------|
| Primary model | `BAAI/bge-m3` (1024-dim, Infinity /embeddings) |
| Fallback model | `BAAI/bge-small-en-v1.5` (384-dim) / OpenRouter — used by rmi-backend chain |
| Container | `infinity-rag` (michaelfeil/infinity) on port 8001 |
| Concurrency | Infinity native batching (`/embeddings` input arrays) |
| Context | 8,000-char truncation per input; 1024-dim output |
| Keep-alive | n/a (stateless HTTP server) |

**Embedding chain (`embeddings.py`)** — strict two-tier, **no hash fallback**
(per T04: hash embeddings returned confidently-wrong results and were removed;
when both backends fail the API raises `RAGUnavailableError` → HTTP 503):

1. **Infinity BAAI/bge-m3** — `POST /embeddings` (OpenAI-compatible) with
   `input` truncated to 8,000 chars, L2-normalized to 1024 dims.
2. **OpenRouter** (only if `OPENROUTER_API_KEY` is set) — same 1024-dim resize.

`RAG_EMBED_BACKEND` env can force `infinity` or `openrouter`; default `auto`
(probe Infinity once per process, cache result).

**Legacy multi-head chain (`crypto_embeddings.CryptoEmbedder`)** — still used
by search/ingest code paths: Infinity → OpenRouter → LocalBGE
(`BAAI/bge-small-en-v1.5`, 384-dim) → HuggingFace (`BAAI/bge-m3`) → hash
(last resort, deprecated). Specialized heads: `embed_token_scam`,
`embed_wallet`, `embed_scam_pattern`, `embed_news`, `embed_contract`
(`ContractCodeEmbedder` — 128-dim structural + 192-dim semantic = 320-dim).

**Batch strategy:** `get_embeddings()` is sequential (Infinity/OpenRouter handle
large batches poorly; memory is kept bounded). Seed scripts use `/embeddings`
with batched `input` arrays; `InfinityBGEEmbedder.embed` sends one request per
text (120 s timeout). Embeddings are cached in Redis 24 h
(`emb:{head}:{sha256(text)[:32]}`).

### 3.3 Retrieval — three-pillar search

`three_pillar_search()` (`search/engine.py`) is the core retrieval path used
by scam-check and search:

| Pillar | Mechanism | Notes |
|--------|-----------|-------|
| **1. Dense** | FAISS ANN index (`app.ann_index`) with Redis brute-force numpy-cosine fallback (`CryptoEmbedder.search`) | Semantic cache checked first (`app.semantic_cache`, Redis) |
| **2. Sparse** | SPLADE + BM25 (`splade_bm25.splade_search`) with BM25 fallback (`sparse_search._sparse_text_search`) | Okapi BM25, k1=1.2, b=0.75, crypto-domain stopwords |
| **3. Entity** | Exact-match entity lookup (`app.entity_extraction`) + knowledge-graph expansion (`app.knowledge_graph`) | Optional; KG expansion is best-effort |

**Fusion:** Reciprocal Rank Fusion (`rrf_fusion._rrr_fuse`, k=60) with
query-adaptive pillar weights from `_classify_query`:

| Query type | dense | sparse | entity |
|------------|------:|-------:|-------:|
| entity_heavy | 0.3 | 0.2 | 0.5 |
| keyword_heavy | 0.3 | 0.6 | 0.1 |
| semantic_heavy | 0.7 | 0.2 | 0.1 |
| balanced (default) | 0.4 | 0.4 | 0.2 |

Entity-matched docs get `entity_boost` multiplier (default 1.5, scam-check uses
2.0). Post-fusion: optional MMR dedup (`mmr_dedup`, λ=0.6), optional
cross-encoder rerank (`use_reranker=False` by default — off in live paths),
and parent-child context expansion (`contextual_chunking`).

**Confidence:** `app.confidence.score_confidence()` scores result sets
(hits, entity matches, score distribution). WIP — see §7.

### 3.4 Ingestion

Two live mechanisms + seed pipelines:

**Scanner → RAG (live, currently idle):**
- `scanner_subscriber.py` — Redis pub/sub on `rmi:rag:ingest`; reconnects with
  5 s backoff; on message: build text → `get_embedding()` (bge-m3) →
  `QdrantVectorStore.insert()` → `threat_intel`.
- `scanner_poller.py` — LPOP polling loop on `rmi:rag:findings_queue` (1 s
  sleep; non-blocking LPOP is used because DragonflyDB has known issues with
  `BLPOP`). Mirrors subscriber processing.
- Both start in the FastAPI lifespan. **As of 2026-07-31 both report
  `running: true` but `ingested_count: 0`** — nothing in rmi-backend is
  publishing to the queues yet (see §7).

**Seed pipelines (for initially-empty collections):**
- `scripts/seed_threat_intel.py` — canonical seeder: 13 static vulnerability
  patterns + 6 historical hacks + Redis seed docs, embedded with **bge-m3
  (1024-dim)**, idempotent (skips existing UUIDs). Invoked via
  `scripts/ingest_threats.py` or `docker compose run --rm rag-ingest`
  (weekly systemd timer).
- `scripts/seed_all.py` — broader seeder (threat_intel + rmi_agent_memory +
  rmi_community + optional backend findings). **Note:** still defaults to
  `qwen3-embed:4b` / 2560-dim — stale vs. the bge-m3 migration; being
  superseded by `seed_threat_intel.py`.

**Nightly news/social (3 AM UTC, `rag_ingestion.py`):** pulls DataBus
news (50 articles), CT rundown (20), market brief, social metrics → Redis
`rag:doc:{collection}:{id}` with 30-day TTL + parent-document store +
delta tracking. Depends on `app.domains.databus.*` (rmi-backend).

**Wallet labels / entities:** `embed_wallet` → `wallet_profiles` →
Redis `rag:idx:wallet_profiles` + per-collection TTLs (see §3.5).

**Dedup strategy:** deterministic UUID v5 point IDs make all upserts
idempotent; Redis `EXISTS` checks skip already-indexed docs; `DeltaTracker`
tracks incremental changes.

**Chunking** (`chunking.py`, `chunking_config.yaml`): per-content-type
strategies — news 512 chars/15% overlap (recursive), scam_report 400/10%
(semantic), contract_code 256/20% (code/AST via `solidity_chunker.py`),
wallet_profile 128/0% (fixed), social_post 256/5% (fixed), market_data 300/10%
(semantic), default 512/15% (recursive).

### 3.5 Caching — L1 / L2 / L3

| Layer | Store | What | Retention |
|-------|-------|------|-----------|
| **L1 — process/Redis hot** | in-process + Redis | embedding cache `emb:{head}:{hash}`, scam-pattern cache `rag:pattern_cache:*`, semantic query cache | 24 h / 30 d / per-entry |
| **L2 — Redis doc store** | Redis | `rag:{collection}:{doc_id}` (JSON) + index sets `rag:idx:{collection}` | per-collection TTL: permanent for `scam_patterns`, `contract_audits`, `transaction_patterns`, `forensic_reports`; 365 d for `wallet_profiles`, `known_scams`, `market_intel`; 90 d `token_analysis`; 30 d `news_articles`, `general` |
| **L3 — B2 permanence** | Backblaze B2 bucket `rmi-rag-permanence` | nightly snapshots `rag_snapshots/{collection}/{ts}_chunkN.json` (5 MB chunks) + `latest.json` pointer; Qdrant API snapshots → `qdrant-snapshots/` | nightly; local 7-day cache (`/data/rag-storage`); opt-in HF mirror |

> **Note:** L3 targets **Backblaze B2** (migrated from Cloudflare R2 on
> 2026-07-31). The fleet's broader backup tiering (B2/HF) is handled by
> fleet-infra; the RAG-specific permanence layer is B2.

---

## 4. API Reference

Authentication: `X-API-Key` header enforced by `RAGAuthMiddleware` when
`RMI_RAG_API_KEY` is set (`/health` and `/ready` exempt). If the key is unset
the API runs open (dev mode — a startup warning is logged).

### 4.1 Retrieval layer — rmi-backend (`/api/v1/rag/v2/*`, LIVE)

| Method | Path | Purpose | Request → Response |
|--------|------|---------|--------------------|
| POST | `/api/v1/rag/v2/search` | Semantic/hybrid search | `SearchRequest {query, collection="scam_intel", top_k=5, min_similarity=0, filters}` → `SearchResponse` with hits + scores |
| POST | `/api/v1/rag/v2/ingest` | Document ingestion | `IngestRequest` → `IngestResult {doc_id, collection, status}` |
| POST | `/api/v1/rag/v2/feedback` | Scanner→RAG feedback | Records feedback, ingests known scam into `known_scams` |
| GET | `/api/v1/rag/v2/stats` | Collection health | `{total_docs, backend, collections: [{name, vector_count}]}` — `backend` shows active embedder (e.g. `infinity:BAAI/bge-m3`) |
| POST | `/api/v1/rag/v2/bulk-ingest` | Batch ingest | max 500 items/call into one collection |

### 4.2 rugbrain service (port 8012)

| Method | Path | Status | Purpose |
|--------|------|--------|---------|
| GET | `/health` | LIVE | Liveness: Qdrant, Redis, subscriber/poller state |
| GET | `/ready` | LIVE | Readiness |
| GET | `/api/v1/search` | LIVE | Semantic search over `threat_intel` (bge-m3 1024-dim, `min_similarity=0.4`) |
| GET | `/api/v1/rag/v2/scam-check` | LIVE | Address scam check. Params: `address`, `chain` (default ethereum), `limit`, `tier=free\|pro`. Three-pillar search over `threat_intel` + `rmi_community`; maps max score + critical/high signal count → `scam_risk: high\|medium\|low\|unknown` + `confidence`. Rate-limited: free 5 req/hr, pro 100/hr via `X-Device-Id` or IP (`429` + `X-RateLimit-Reset`) |
| POST | `/api/v1/rag/investigate` | ⚠️ WIP — currently 500 | Deep investigation narratives (`src/app/investigation_narratives.py`, restore in progress) |
| POST | `/api/v1/rag/confidence` | ⚠️ WIP — currently 500 | Confidence scoring (`src/app/confidence.py`, restore in progress) |
| POST | `/api/v1/rag/chunk-solidity` | LIVE | Solidity AST chunking |
| POST | `/api/v1/scanner/deployer-risk` | ⚠️ WIP | Deployer risk scoring (`src/app/deployer_reputation.py`) |
| POST | `/api/v1/rag/cross-chain` | ⚠️ WIP | Cross-chain identity resolution (`src/app/cross_chain.py`) |
| POST | `/api/v1/rag/image-analysis` | LIVE | Multimodal image description |
| POST | `/api/v1/rag/logo-check` | LIVE | Logo theft detection (perceptual hash) |
| GET | `/api/v1/rag/journalism/research` | LIVE | Deep-dive research brief across `threat_intel`, `rmi_agent_memory`, `rmi_community` |
| GET | `/api/v1/rag/graph-communities` | DELETED (2026-08-16) | Route removed with `graph_rag.py`; GraphRAG design contract → `docs/graphrag-architecture.md` (rmi-backend Neo4j) |
| POST | `/api/v1/rag/watch` / GET `/api/v1/rag/watches` | LIVE | Streaming watch creation/list |
| GET | `/api/v1/rag/metrics` | ⚠️ defined, not mounted | Prometheus-style RAG metrics (`metrics_api.py`; not yet wired into `main.py`) |
| GET | `/docs`, `/redoc`, `/openapi.json` | LIVE | OpenAPI 3.1 docs |

---

## 5. Data Flow

### 5.1 Scan result → searchable

```
rmi-backend UnifiedTokenScanner (high/critical risk)
  → Redis PUBLISH rmi:rag:ingest          (subscriber path)
  → Redis LPUSH  rmi:rag:findings_queue   (poller path, Dragonfly-safe)
  → scanner_subscriber / scanner_poller
  → get_embedding()  (bge-m3, 1024-dim)
  → QdrantVectorStore.insert() → Qdrant threat_intel   (id: scanner_{chain}_{addr})
  → searchable via GET /api/v1/search and /api/v1/rag/v2/scam-check
```

**Current state:** both consumers are subscribed and healthy, but
**`ingested_count: 0`** — rmi-backend is not yet publishing findings. Scanner
findings remain in Postgres (existing behavior; no data loss). See §7 P0.

### 5.2 News article → searchable

```
DataBus news aggregator (200+ RSS feeds: CoinTelegraph, Decrypt, The Block, X)
  → app/rag/rag_ingestion nightly_rag_index (3 AM UTC)
  → Redis rag:doc:news_articles:{hash12}  +  parent store  +  delta tracker
  → searchable via rmi-backend /api/v1/rag/v2/search
```

Firehose (`firehose/news.py`) provides an on-demand pull path (DataBus
`/api/v1/databus/fetch/news` → `news_articles`, prediction markets →
`market_intel`). **Gap:** news collections are Redis-only today; the Qdrant
`news` collection has not been created (see §7).

### 5.3 Wallet label → searchable

```
Wallet label / profile
  → CryptoEmbedder.embed_wallet()   (semantic 1024 + wallet features + behavioral)
  → Redis rag:idx:wallet_profiles + rag:wallet_profiles:{id}  (365 d TTL)
  → searchable via three-pillar search (entity pillar + dense)
```

Cross-chain identity (`src/app/cross_chain.py`) and investigation narratives
(`src/app/investigation_narratives.py`) consume wallet/entity data; both are
part of the `src/app/` restore (WIP).

### 5.4 Backup & permanence (nightly → B2)

```
Qdrant collections                        Redis-backed collections
  │ backup_qdrant.py (3:30 AM UTC cron)     │ permanence.snapshot_all()
  ▼                                         ▼
Qdrant API snapshot  → B2 rmi-rag-permanence  JSON chunks (≤5 MB) → B2
  qdrant-snapshots/{coll}_{date}.snapshot    rag_snapshots/{coll}/{ts}_chunkN.json
                                            + latest.json pointer
                                            + local 7-day cache + opt-in HF mirror

Recovery:
  Qdrant: restore snapshot via Qdrant API (manual)
  Redis:  permanence.restore_all() — reads latest.json, rehydrates Redis
```

**Current state:** the permanence path (Redis→B2) is implemented; the Qdrant
snapshot backup now uploads to B2 (`rmi-rag-permanence`) via b2sdk.

---

## 6. Operations

### 6.1 Monitoring

| Signal | Source | Status |
|--------|--------|--------|
| Qdrant/Redis health | `GET /health` | LIVE (per-request probes + startup probes) |
| Subscriber/poller state | `GET /health` → `subscriber_stats`, `poller_stats` (`running`, `connected`, `ingested_count`) | LIVE |
| Collection sizes + embedder backend | `GET /api/v1/rag/v2/stats` | LIVE |
| Collection sizes, ingest rate, pattern mix | Grafana dashboard `grafana/rag-dashboard.json` (Prometheus `rugbrain_*` metrics) | PARTIAL — metrics not yet fully exported |
| Queue depth (`rmi:rag:ingest`, `rmi:rag:findings_queue`) | redis-cli / Redis dashboard | manual |
| Embedding/retrieval latency | `metrics_api.py` → `/api/v1/rag/metrics` | ⚠️ defined, not mounted |

**Operational gaps (from TODO 2026-07-31):** no query-latency/hit-rate
metrics yet; `rag-monitor` script has a syntax error; health dashboard shows
`ingested_count: 0` (either fix or remove).

### 6.2 Backup
- Redis-backed collections → B2 `rag_snapshots/` nightly (`permanence.nightly_cycle`).
- Qdrant API snapshots → B2 `qdrant-snapshots/` nightly via cron
  (`backup_qdrant.py`; local retention 3/collection).
- Fleet backup (local → B2 encrypted → HF) is handled outside this repo by
  fleet-infra cron jobs.

### 6.3 Recovery
1. Restore Redis collections: run `permanence.restore_all()` (needs
   `B2_APPLICATION_KEY_ID`/`B2_APPLICATION_KEY`); rehydrates `rag:{collection}:{id}`
   + index sets from `latest.json` pointers.
2. Restore Qdrant: download `qdrant-snapshots/{coll}_{date}.snapshot` from B2,
   upload via Qdrant API (`/collections/{coll}/snapshots/upload`).
3. Re-seed empty collections: `docker compose run --rm rag-ingest`
   (`seed_threat_intel.py`, idempotent).

### 6.4 Scaling
- **Infinity:** stateless OpenAI-compatible server on port 8001; concurrency
  bounded by Infinity's native batching over `/embeddings` and `/rerank`
  (BAAI/bge-m3 1024-dim + BAAI/bge-reranker-v2-m3).
- **Qdrant:** single node, collections created with default (unsharded)
  config; sharding/replication not configured — revisit when collections
  exceed a single node.
- **Redis:** singleton async pool with ping-revalidate (`search_service._get_redis`).

---

## 7. Known Issues & Roadmap

### 7.1 Current gaps (honest status, 2026-07-31)

1. **All vector stores empty** — `threat_intel`, `rmi_agent_memory`,
   `rmi_community` exist with 0 points; seeding is in progress
   (`scripts/seed_threat_intel.py` — untracked WIP being run now).
2. **Configured collections missing** — `scanner`, `wallet`, `news`,
   `research`, `scams` don't exist in Qdrant; must be created at 1024-dim.
3. **Scanner ingestion idle** — subscriber/poller running but nothing
   publishes to Redis queues (`ingested_count: 0`).
4. **rugbrain-api 500s** — `/api/v1/rag/investigate` and
   `/api/v1/rag/confidence` fail with `ModuleNotFoundError: No module named
   'app'`; the `src/app/` package (confidence, cross_chain,
   deployer_reputation, investigation_narratives) is being restored into the
   repo — packaging/PYTHONPATH must be fixed.
5. ~~Qdrant nightly backup failing (missing R2 credentials)~~ — fixed by the
   R2→B2 migration (2026-07-31); snapshots now upload to B2.
6. **Embedder fallback latency** — bge-m3 (1024-dim) is primary; nomic-embed-text
   (768-dim) is the backend fallback. Cross-model dimension mismatch is a real
   risk if a single collection mixes dims — collections are created per
   first-write dimension.
7. **Stale references** — `seed_all.py` still defaults to qwen3-embed:4b
   (2560-dim); `README-RAG-API.md` still documents nomic-embed-text as primary.
8. **Code patterns in Qdrant (migration)** — contract-code embeddings now
   migrate from the legacy ChromaDB store into rmi-codebase-mcp Qdrant
   (bge-m3 1024-dim via Infinity, `security_patterns` collection).
9. **Test/quality debt** — 1 failing test (`test_qdrant_store.py`), coverage
   ~10% (60% threshold unenforced), 42 ruff errors, `make lint`/`make test`
   not green.

### 7.2 Roadmap

**P0 — Make it real (in progress)**
- [ ] Finish seeding `threat_intel` (+ agent memory, community) with bge-m3 at
      1024-dim; verify via `/api/v1/rag/v2/stats`
- [ ] Recreate configured collections (`scanner`, `wallet`, `news`, `research`,
      `scams`) at 1024-dim
- [ ] Wire rmi-backend scanner → Redis publish so subscriber/poller
      `ingested_count` moves
- [x] Fix Qdrant nightly backup (R2→B2 migration); verify snapshot restore
- [ ] Restore `src/app/` packaging → unblock `/investigate` + `/confidence`
- [ ] Consolidate stores: one canonical collection set across
      rmi-backend and rugbrain; retire stale seed paths

**P1 — Retrieval quality**
- [ ] BM25 sparse: verify SPLADE+BM25 wiring end-to-end in live search paths
      (`splade_bm25.py` exists; needs validation + tests)
- [ ] Cross-encoder rerank: enable `use_reranker` on high-traffic endpoints
      (`cross_encoder.py` exists; default off)
- [ ] Evaluation pipeline: RAGAS harness exists (`eval_ragas.py`,
      `evaluation.py`) — needs a labeled eval set and CI gate

**P2 — Entity & multimodal expansion**
- [ ] Neo4j/knowledge-graph entity expansion (`app.knowledge_graph` external;
      formalize graph store)
- [ ] Image pipeline: multimodal + logo-theft DB (`multimodal.py` exists)
- [ ] Contract-code embeddings in Qdrant (`ContractCodeEmbedder` 320-dim
      combined structural+semantic); align rmi-codebase-mcp to bge-m3
- [ ] Metrics: query latency, hit rate, collection-size gauges in Prometheus
      + Grafana

---

## Appendix — Key Env Vars

| Var | Default | Purpose |
|-----|---------|---------|
| `QDRANT_URL` | `http://rmi-qdrant:6333` | Qdrant REST endpoint |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | `rmi-redis` / `6379` | Redis (DragonflyDB) connection |
| `INFINITY_URL` | `http://infinity-rag:8001` | Infinity embedding/rerank endpoint |
| `RAG_INFINITY_EMBED_MODEL` | `BAAI/bge-m3` | Primary embedder |
| `RAG_EMBED_BACKEND` | `auto` | `auto` \| `infinity` \| `openrouter` |
| `RAG_EMBED_MODEL` / `RAG_EMBED_VERSION` | `bge-m3` / `1.1` | Embedding version stamp (ADR-0007) |
| `RAG_COLLECTIONS` | (unset → 3 defaults) | Comma-separated collection list |
| `RAG_EMBED_DIMS` | (unset → defaults) | `model:dim` map |
| `RMI_RAG_API_KEY` | (unset → open) | X-API-Key auth for the rugbrain API |
| `B2_APPLICATION_KEY_ID` / `B2_APPLICATION_KEY` / `B2_BUCKET_NAME` | — / `rmi-rag-permanence` | B2 permanence credentials |
| `RAG_HF_MIRROR` / `RAG_HF_REPO` | `false` | Opt-in HuggingFace mirror |

---

*Update this document whenever components change. The TODO audit
(`TODO.md`, 2026-07-31) is the source of truth for current gaps.*

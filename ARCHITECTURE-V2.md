# RMI RAG — Consolidation Blueprint (ARCHITECTURE-V2)

**Status:** implementation spec (v2) · supersedes the "two RAGs" status quo
**Owner:** crmuncher · **Date:** 2026-08-01 · **Repo:** `RugMunchMedia/rmi-rag`
**Scope:** pure design/planning. This file is the contract the 6 implementing agents follow. No code was changed to produce it.

> **How to use this document**
> - Sections 1–8 are the normative design. Agents MUST NOT deviate without a written note in the PR.
> - Section 10 (sequencing) is the order of operations. Do not skip a phase; the proxy-first order protects prod.
> - Sections 3.3 / 3.4 are the authoritative file-disposition tables. Every `.py` file in the RAG surface is classified.
> - Facts marked `[verified]` were confirmed against the repos on 2026-08-01. Facts marked `[decision]` are architect decisions that fill gaps in the original brief — agents should treat them as binding unless the reviewer objects.

---

## 0. Target State (one picture)

```
                    ┌─────────────────────────────────────────────┐
                    │              rmi-backend                     │
                    │  auth · rate-limit · API routing · scanners  │
                    │                                              │
  bot ─────────────►│  /api/v1/rag/v2/*   (HTTP proxy)             │
  frontend chat ───►│  /api/v1/rag/v2/search                       │
  scanner ─────────►│  domains/scanner/rag_scam_check.py           │
                    │        │                                     │
                    │        ▼  internal HTTP (rmi_network)        │
                    └────────┼─────────────────────────────────────┘
                             │  POST http://rmi-rag-api:8012/...
                             ▼
                    ┌─────────────────────────────────────────────┐
                    │              rmi-rag-api  (:8012)            │
                    │  THE single RAG service                      │
                    │  embedder.py → store.py (Qdrant)            │
                    │  retrieval.py ← api/* · ingestion/*         │
                    │  evaluation/eval.py (RAGAS)                 │
                    └─────────────────────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────────────────────────────────┐
                    │  Qdrant (rmi-qdrant:6333) · Ollama BGE-M3   │
                    │  collections: scanner · news · wallet       │
                    └─────────────────────────────────────────────┘
```

**Non-negotiables**
1. ONE embedding stack: BGE-M3 (1024d) via Ollama, `embedder.py`. No hash fallback, no dual-dim, no "tiers".
2. ONE store: Qdrant, `store.py` (wraps `qdrant_store.py`). No numpy ANN, no ChromaDB, no Redis vectors.
3. ONE search path: dense + keyword filter + RRF, `retrieval.py`. No three-pillar facade.
4. Backend never embeds, never queries vectors. It proxies HTTP.
5. Fail-closed. Infra failure → 503, never empty results, never fake vectors.

---

## 1. Service Boundary

| Concern | Lives in | Why |
|---|---|---|
| Embedding (BGE-M3 via Ollama) | rmi-rag | single model, single dim (1024), owns Ollama latency/retry |
| Vector storage (Qdrant) | rmi-rag | single writer to collections; backend must not touch Qdrant |
| Retrieval (dense + keyword + RRF) | rmi-rag | one search implementation, one ranking contract |
| Ingestion (scanner/news/wallet → embed → upsert) | rmi-rag | embedding is co-located with storage |
| Evaluation (RAGAS) | rmi-rag | needs direct retrieval+store access |
| API routes for RAG (`/api/v1/*`) | rmi-rag-api | the service exposes its own HTTP surface |
| Auth / API keys / tenant | rmi-backend | existing middleware; never duplicated in rmi-rag |
| Rate limiting (user-facing) | rmi-backend | existing Redis limiter stays the gate |
| Public API routing (`/api/v1/rag/v2/*` → proxy) | rmi-backend | keeps public contract stable for bot/SDK |
| Scanner integration (finding → ingest trigger) | rmi-backend (emit) + rmi-rag (consume) | backend publishes to Redis `rmi:rag:ingest`; rmi-rag `scanner_subscriber` consumes |
| Scam-check orchestration (pattern match + LLM reasoning) | rmi-backend (orchestrate) + rmi-rag (retrieve) | backend keeps its domain logic; vector retrieval is HTTP |
| Redis-based aux features (watches, realtime alerts) | rmi-rag (its own Redis) | not part of retrieval; see §3.3 disposition |

**Rule:** a file in rmi-backend may NOT import `app.rag.*`, `app.rag_service.*`, `app.rag_firehose.*`, `qdrant_client`, or `rmi_rag`. All RAG access is `RagApiClient` HTTP. [decision]

---

## 2. Module Map for rmi-rag (target tree)

```
src/rmi_rag/
├── config.py              # NEW [decision]: settings (QDRANT_URL, OLLAMA_URL, collection names, timeouts).
│                          #   Only module embedder/store/retrieval/ingestion/api may import.
├── embedder.py            # Single embedder: BGE-M3 via Ollama (/api/embeddings). 1024d.
│                          #   KEEP (verified working) — delete qwen3 legacy enum entries.
├── store.py               # NEW thin public wrapper over qdrant_store.py: get_collection(name),
│                          #   upsert(points), search(query, filter, limit). Collection list is config.
├── retrieval.py           # NEW: dense search (store.query_vector) + keyword filter + RRF.
│                          #   Absorbs: search/ranking.py logic, rrf_fusion.py, search/filters.py filter-building.
│                          #   Exposes: hybrid_search(query, collection, filters, limit), rerank optional stub.
├── ingestion/
│   ├── __init__.py        # NEW
│   ├── scanner.py         # scanner findings → embed → upsert scanner collection.
│   │                      #   Absorbs scanner_subscriber.py handler + scanner_poller.py loop + feedback.py upsert.
│   ├── news.py            # news articles → embed → news collection.  Absorbs news_to_rag ingest path.
│   └── wallet.py          # wallet labels → embed → wallet collection.
├── api/
│   ├── __init__.py        # NEW
│   ├── search.py          # POST /api/v1/search
│   ├── scam_check.py      # POST /api/v1/rag/v2/scam-check
│   ├── ingest.py          # POST /api/v1/ingest
│   ├── investigate.py     # POST /api/v1/investigate
│   ├── confidence.py      # POST /api/v1/confidence
│   └── admin.py           # GET /api/v1/stats · /health · /ready
├── evaluation/
│   ├── __init__.py        # NEW
│   └── eval.py            # RAGAS pipeline. Absorbs eval_ragas.py (+ evaluation.py if distinct).
├── main.py                # FastAPI app: lifespan (embedder/store init, subscriber start),
│                          #   import_guard (transitional, §4.2), middleware, mounts api/* routers.
└── __init__.py            # SLIM: re-export ONLY kept modules. Remove all deleted-module exports (§3.1.3).
```

**Rules of thumb for the implementer**
- `qdrant_store.py` is KEPT and becomes the internals of `store.py` (or is renamed to `store.py` in-place; either is fine — do not keep two public store modules).
- `scanner_subscriber.py` / `scanner_poller.py` are KEPT as working code; their *content* migrates into `ingestion/scanner.py` or is imported from there. They must not remain top-level modules that bypass `ingestion/`.
- `endpoints.py` is SPLIT: its routes move to `api/*.py` per the route-triage table (§8.2), then the file is deleted.
- The `search/` package (`engine.py`, `filters.py`, `ranking.py`, `_state.py`, `__init__.py`) is DELETED; `retrieval.py` absorbs the live parts (`ranking.py` scoring + RRF). `search_similar.py` was already confirmed to be a duplicate of `search/engine.py` [verified: 0 importers] — it dies with the package.

---

## 3. What Gets DELETED

### 3.1 In `rmi-rag` — DELETE (17 files + 2 packages)

| File | Reason (verified) |
|---|---|
| `src/rmi_rag/search_similar.py` | duplicate of `search/engine.py`; 0 importers |
| `src/rmi_rag/search/` (whole package) | `engine.py` = broken three-pillar facade; `filters.py`/`ranking.py`/`_state.py` absorbed into `retrieval.py`; `__init__.py` dies with package |
| `src/rmi_rag/embedding_router.py` | dead; broken import |
| `src/rmi_rag/embed_tiers.py` | 0 importers |
| `src/rmi_rag/legacy_supabase.py` | dead |
| `src/rmi_rag/embed_utils.py` | 0 importers |
| `src/rmi_rag/query_classifier.py` | duplicate of `search/ranking.py` (now in retrieval) |
| `src/rmi_rag/query_rewriter.py` | only importer is `__init__.py` [verified] → dead once `__init__` slims |
| `src/rmi_rag/metrics_api.py` | 0 importers; never mounted |
| `src/rmi_rag/agentic.py` | 0 importers; also imports `app.*` (would need guard) |
| `src/rmi_rag/tool.py` | 0 importers |
| `src/rmi_rag/chunking.py` | 0 importers (chunking lives in `app_chunking.py`/`contextual_chunking.py` — see 3.3) |
| `src/rmi_rag/permanence.py` | 0 importers; broken `app.*` imports |
| `src/rmi_rag/splade_bm25.py` | fake SPLADE (O(n) brute force); only importer was `search/engine.py` |
| `src/rmi_rag/cross_encoder.py` | fake cross-encoder (bi-encoder cosine); only live importer was `search/engine.py` + `__init__` |
| `src/rmi_rag/crypto_embeddings.py` | redundant multi-head embedder hub — see cascade map §3.1.1 |
| `src/rmi_rag/bge_embedders.py` | redundant; superseded by `embedder.py` |
| `src/rmi_rag/rag_provider.py` | broken imports; fake model string |
| `src/rmi_rag/rag_ingestion.py` | `_embed_batch` never embeds [verified bug] |
| `src/rmi_rag/embeddings.py` | legacy dual-backend wrapper; superseded by `embedder.py` — see redirect map §3.1.2 |
| `src/rmi_rag/search_service.py` | facade re-exporting the deleted `search/` package; all callers redirected |
| `src/app/` (whole compat package, 9 files) | the `app.*` shim that exists so rmi-rag could fake rmi-backend imports (`src/app/rag_service.py` re-exports `search.engine.three_pillar_search`). With `search/` deleted and the no-`app.*` rule (§6), it is dead weight. [verified] |
| `src/rmi_rag/rrf_fusion.py` | real logic, 1.4 KB — do NOT delete the logic; fold into `retrieval.py` [decision] |

#### 3.1.1 Cascade map — `crypto_embeddings.py` has 10 importers [verified]
Deleting it must happen in the same commit as its importers. Each importer's disposition:
`agentic.py` (del) · `bundle_cluster.py` (del, §3.3) · `historical.py` (del) · `multimodal.py` (del) · `streaming.py` (del) · `tool.py` (del) · `evaluation.py` (merge→eval) · `embedding_finetune.py` (del) · `app_chunking.py` (del) · `citations.py` (keep, rewire to `embedder.py`).
**Ordering rule:** delete `crypto_embeddings.py` in the SAME commit as the last of its importers, or the repo breaks. Grep gate: `grep -rn "crypto_embeddings" src/ tests/ scripts/` must return 0.

#### 3.1.2 Redirect map — `embeddings.py` callers [verified]
`get_embedding / get_embeddings / current_backend / EMBED_*` are imported by:
`src/app/*` (del) · `endpoints.py` (rewrite to `embedder.py`) · `__init__.py` (slim) · `scanner_subscriber.py` (→ `embedder.py`) · `scanner_poller.py` (→ `embedder.py`) · `journalism.py` (see 3.3) · `scripts/seed_threat_intel.py` (→ `embedder.py`).
All redirect to `embedder.py`'s single async `embed()`. Note `embeddings.py` already implements "fail loudly, no hash fallback" [verified docstring] — carry that behavior into `embedder.py` (it is the error contract, §7).

#### 3.1.3 `__init__.py` slimming [verified]
Current `__init__.py` re-exports from `cross_encoder`, `embeddings`, `incremental_indexer`, `parent_retriever`, `query_rewriter`, `search_service` — all deleted. Target `__init__.py` exports ONLY: `embedder`, `store`, `retrieval`, `ingestion`, `api`, `evaluation`, `main` (and `__version__`).

### 3.2 In `rmi-backend` — DELETE

| Path | Reason (verified) |
|---|---|
| `app/rag_service/` (whole dir, 6 files) | deprecated shim; 31 files still reference `rag_service` [verified] — all redirected in Phase 2 (§10) before this dir dies |
| `app/rag_firehose/` (whole dir, 5 files) | dead code, zero callers |
| `app/rag_historical.py` | dead |
| `app/rag_evaluation.py` | dead |
| `app/ragas_eval.py` | dead (RAGAS lives in rmi-rag `evaluation/eval.py`) |
| `app/rag_permanence.py` | dead |
| `app/rag_feedback.py` | dead (feedback = ingest-into-known_scams, §5) |
| `app/rag_agentic.py`, `app/rag_chunking.py`, `app/rag_metrics_api.py`, `app/rag_observability.py`, `app/rag_tool.py` | dead facades |
| `app/services/supabase_rag.py` | dead |
| `app/databus/rag_provider.py` | broken imports |
| `app/databus/rag_ingestion.py` | `_embed_batch` broken; also imports `app.rag.*` |
| `app/rag/` (whole dir, 17 files) | **the second implementation** (numpy ANN over Redis). Not on the original delete list, but required by the Goal ("No double implementations"). Retired LAST, after every caller is on the HTTP client (§10 Phase 3). Files: ann_index.py, chunking.py, config.py, embedder.py, embeddings.py, engine.py, feedback.py, ingestion.py, ingestion_models.py, intelligence.py, llama_adapter.py, migration_router.py, models.py, processor.py, quality.py, rate_limiter.py, retrieval.py, service.py. [decision] |

**Callers of `app.rag.*` that must move to `RagApiClient` before `app/rag/` dies [verified]:** `app/api/v1/rag/search.py` (becomes the proxy), `app/bulletin_board/badges.py`, `app/catalog/service.py`, `app/domains/scanner/rag_scam_check.py`, `app/domains/scanner/service/scan.py`, `app/domains/scanner/service/scoring.py`, `app/domains/scanner/shared/deployer_labeler.py`, `app/routers/address_profiler.py`, `app/routers/pryscraper_bridge.py`, plus everything in `app/rag/` itself.

### 3.3 Full disposition table — every `.py` in `rmi-rag/src/rmi_rag/` (authoritative)

| Module | Disposition |
|---|---|
| `__init__.py` | SLIM (3.1.3) |
| `__main__.py` | KEEP (entrypoint) |
| `agentic.py` | DELETE |
| `analytics_storage.py` | DELETE [decision: no target-state consumer; if a kept module needs history, it must own its storage] |
| `app_chunking.py` | DELETE [decision: chunking strategy is owned by ingestion callers; `contextual_chunking.py` also DELETE] |
| `bge_embedders.py` | DELETE |
| `bundle_cluster.py` | DELETE |
| `chunking.py` | DELETE |
| `citations.py` | KEEP→ move under `retrieval.py` support or `api/investigate.py` [decision: only if the scanner citation path is confirmed alive; otherwise DELETE — assign agent to verify with `grep -rn "citations"` in rmi-backend] |
| `contextual_chunking.py` | DELETE [decision] |
| `cross_chain.py` | DELETE [decision: no kept consumer; cross-chain retrieval is out of RAG core scope] |
| `cross_encoder.py` | DELETE (fake) |
| `crypto_embeddings.py` | DELETE (cascade §3.1.1) |
| `embed_tiers.py` | DELETE |
| `embed_utils.py` | DELETE |
| `embedder.py` | KEEP + fix (§4.1) |
| `embedding_finetune.py` | DELETE |
| `embedding_router.py` | DELETE |
| `embeddings.py` | DELETE (redirect §3.1.2) |
| `endpoints.py` | SPLIT into `api/*` then DELETE (§8.2) |
| `eval_ragas.py` | KEEP→ `evaluation/eval.py` |
| `evaluation.py` | MERGE→ `evaluation/eval.py` if distinct pipeline; else DELETE [decision: agent verifies overlap] |
| `feedback.py` | DELETE as standalone → confirmation-upsert folded into `ingestion/scanner.py` [decision] |
| `feature_extractors.py` | DELETE [decision] |
| `firehose/` | DELETE [decision: no target consumer; news ingestion goes through `ingestion/news.py`] |
| `github_feeder.py` | DELETE [decision] |
| `graph_rag.py` | DELETE [decision: graph retrieval is not in the target module map; revisit as separate feature] |
| `historical.py` | DELETE |
| `import_guard.py` | KEEP transitional (§4.2) → DELETE in Phase 5 |
| `incremental_indexer.py` | DELETE [decision: delta tracking is a backend/cache concern; Qdrant upserts are idempotent by point id] |
| `journalism.py` | RETAIN for now only if the `/api/v1/rag/journalism/research` route is kept (§8.2); else DELETE [decision: default KEEP route, so KEEP module but rewire to `embedder.py`/`retrieval.py`, drop `app.*`] |
| `legacy_supabase.py` | DELETE |
| `main.py` | KEEP + fix (§4.3) |
| `metrics_api.py` | DELETE |
| `multimodal.py` | DELETE |
| `observability.py` | KEEP [decision: embed/retrieve tracing is core; rewire to kept modules, drop `app.*`] |
| `parent_retriever.py` | DELETE [decision: parent-context retrieval becomes part of `retrieval.py` if needed; not a top-level module] |
| `pattern_extractor.py` | KEEP→ `ingestion/news.py` support (LLM pattern extraction feeds news ingestion) [decision] |
| `permanence.py` | DELETE |
| `qdrant_store.py` | KEEP (becomes `store.py` internals) |
| `query_classifier.py` | DELETE |
| `query_rewriter.py` | DELETE |
| `rag_ingestion.py` | DELETE |
| `rag_provider.py` | DELETE |
| `realtime_alerts.py` | DELETE [decision: watch-alerts is a bot/backend feature, not RAG core; bot already has its own alerting] |
| `rrf_fusion.py` | FOLD→ `retrieval.py` |
| `scam_detection.py` | KEEP→ `api/scam_check.py` support [verified: imports search_service — rewire] |
| `scanner_poller.py` | KEEP→ `ingestion/scanner.py` |
| `scanner_subscriber.py` | KEEP→ `ingestion/scanner.py` |
| `search/` | DELETE (package) |
| `search_service.py` | DELETE (facade) |
| `search_similar.py` | DELETE |
| `seed_scams.py` | DELETE [decision: seed data via scripts/ only] |
| `solidity_chunker.py` | DELETE [decision] |
| `sparse_search.py` | DELETE [decision: keyword filter in `retrieval.py` replaces it; no SPLADE] |
| `splade_bm25.py` | DELETE (fake) |
| `streaming.py` | DELETE [decision: streaming response is backend proxy concern (SSE passthrough), not rmi-rag core] |
| `tool.py` | DELETE |

### 3.4 Full disposition table — `rmi-backend` RAG surface

| Path | Disposition |
|---|---|
| `app/api/v1/rag/search.py` | KEEP + REWRITE as HTTP proxy (§5) |
| `app/domains/scanner/rag_scam_check.py` | KEEP + fix C1 (§4.4) |
| `app/routers/news.py` | KEEP (Query bug already fixed) — verify no `app.rag_service` import remains |
| `app/rag/` | DELETE (Phase 3) |
| `app/rag_service/` | DELETE (Phase 3) |
| `app/rag_firehose/` | DELETE (Phase 1) |
| `app/rag_*.py` (10 top-level facades) | DELETE (Phase 1) |
| `app/services/supabase_rag.py` | DELETE (Phase 1) |
| `app/databus/rag_provider.py`, `app/databus/rag_ingestion.py` | DELETE (Phase 1) |
| `app/rag_client.py` (NEW) | CREATE: the single HTTP client (§5.1) |

---

## 4. What Gets KEPT and FIXED

### 4.1 `rmi-rag/embedder.py` — KEEP, 3 fixes
1. Remove the retired `QWEN3_4B` enum entry + `_BACKEND_DIMS` dead path (BGE-M3 1024d only). [verified it is already the documented primary]
2. Carry the "fail loudly, no hash fallback" semantics already in `embeddings.py` into `embed()` (raise `EmbeddingError` on HTTP error/timeout; never synthesize vectors).
3. Expose one canonical `async def embed(texts: list[str]) -> list[list[float]]`; add `embed_one()` convenience. Update `scanner_subscriber/poller`, `scripts/*`, `endpoints.py` to use it.

### 4.2 `rmi-rag/import_guard.py` — KEEP transitional, 2 fixes
1. Finish the `__await__` / `__iter__` annotations on `_StubCallable` (mypy strict currently fails) [verified: it is a `dict` subclass with callable/awaitable/iterable stubbing].
2. Keep installed in `main.py` lifespan ONLY while any kept module still references `app.*`. Enforce the §6 no-`app.*` rule; when the final `grep -rn "app\." src/rmi_rag/` (excluding import_guard itself) returns 0, DELETE the guard and its install call (Phase 5).

### 4.3 `rmi-rag/main.py` — KEEP, 3 fixes
1. Lifespan: init `embedder` + `store` (Qdrant client) once; start `scanner_subscriber` background task; stop cleanly on shutdown.
2. Mount the `api/*` routers (search, scam_check, ingest, investigate, confidence, admin) — replaces `endpoints.py` mounting.
3. Keep `/health` + `/ready` (Dockerfile healthcheck already probes `/ready` on :8012 [verified]).

### 4.4 `rmi-backend/app/domains/scanner/rag_scam_check.py` — KEEP, fix C1
C1 = the module queries ChromaDB directly with a pattern-match fallback [verified: docstring says "ChromaDB primary, pattern-match fallback"]. Fix: replace the ChromaDB path with `POST http://rmi-rag-api:8012/api/v1/rag/v2/scam-check` via `RagApiClient`. Keep the pattern-match + neutral fallbacks ONLY as behavior at the backend orchestration layer (LLM reasoning), never as a vector-substitute.

### 4.5 `rmi-rag/scanner_subscriber.py` / `scanner_poller.py` — KEEP (working) [verified]
- Subscriber: Redis `rmi:rag:ingest` → embed → upsert Qdrant. Fix stale docstring (says 2560-dim; embedder is 1024d BGE-M3) and rewire `get_embedding()` → `embedder.embed()`. Then relocate into `ingestion/scanner.py`.
- Poller: keep loop; same rewiring.

### 4.6 Backend routers — KEEP, redirect
All 31 files referencing `rag_service` [verified count] move to `RagApiClient` calls. Public route shapes and Pydantic models in `app/api/v1/rag/search.py` are unchanged (bot + SDK depend on them [verified: rugmunchbot bot.py, backend telegram handlers, rugmunch_sdk all call `/api/v1/rag/v2/search`]).

---

## 5. Consumer Routing

**Single rule:** every RAG consumer reaches ONE endpoint — `POST http://rmi-rag-api:8012/api/v1/search` (or a sibling `api/*` route). Everything goes through the backend proxy.

| Consumer | Chain |
|---|---|
| Bot (`/research`, scam checks) | bot → `POST /api/v1/rag/v2/search` (backend) → `RagApiClient` → `POST rmi-rag-api:8012/api/v1/search` |
| Scanner (scam-check, citations, deployer risk) | scanner modules → `RagApiClient` → `POST rmi-rag-api:8012/api/v1/rag/v2/scam-check` / `/api/v1/search` |
| Frontend chat (currently unwired) | frontend → `POST /api/v1/rag/v2/search` (backend, existing auth) → `RagApiClient` → rmi-rag-api |
| Ingest (scanner findings, news, wallet labels) | backend emits Redis `rmi:rag:ingest` → `scanner_subscriber` (rmi-rag) embeds+upserts; OR backend calls `POST /api/v1/ingest` |
| Feedback (confirmed scam) | backend → `POST /api/v1/ingest` with `collection: known_scams` (replaces `app/rag_feedback.py`) |

### 5.1 `RagApiClient` (new, in rmi-backend)
- One file: `app/rag_client.py`. `httpx.AsyncClient` with base URL from env `RMI_RAG_API_URL` (default `http://rmi-rag-api:8012`), timeout 30s connect / 60s read, 2 retries with backoff on 5xx/network, `raise_for_status`.
- Methods mirror the rmi-rag-api surface: `search()`, `scam_check()`, `ingest()`, `investigate()`, `confidence()`, `stats()`, `health()`.
- Fail-closed: any infra failure raises `RagUnavailableError`; backend routes convert to 503. Never returns empty/fallback payloads (see §7).
- Deployment: backend and rmi-rag-api MUST share the `rmi_network` Docker network; set `RMI_RAG_API_URL=http://rmi-rag-api:8012` in backend env. [verified compose: rag-api is on `rmi_network`, ports `127.0.0.1:8012:8012`, `QDRANT_URL=http://rmi-qdrant:6333`]
- Optional defense-in-depth: shared `X-RMI-SERVICE-TOKEN` from env on both sides; optional, disabled by default [decision].

---

## 6. Module Boundaries (import rules)

| Module | MAY import | MUST NOT import |
|---|---|---|
| `embedder.py` | `config`, `httpx`, stdlib | `app.*`, any other `rmi_rag.*` |
| `store.py` | `config`, `qdrant_client` (via `qdrant_store.py` internals) | `embedder`, `retrieval`, `api`, `ingestion` |
| `retrieval.py` | `store`, `embedder`, `config` | `api`, `ingestion` |
| `ingestion/*.py` | `store`, `embedder`, `config` | `api`, `retrieval` |
| `api/*.py` | `retrieval`, `ingestion`, `config`, `observability` | `store` directly (use `retrieval`/`ingestion`) |
| `evaluation/*.py` | `retrieval`, `store`, `config` | `api` |
| `main.py` | everything (composition root only) | — |
| rmi-backend `app/**` | `app.rag_client` only | `app.rag*`, `app.rag_service*`, `app.rag_firehose*`, `qdrant_client`, `rmi_rag`, `chromadb` (rag_scam_check must drop it) |

**Enforcement (add to CI, Phase 1):**
1. `grep -rnE "from (app\.|rmi_rag\.)" src/rmi_rag/embedder.py src/rmi_rag/store.py src/rmi_rag/retrieval.py` → 0 matches (import_guard excluded by path).
2. `grep -rn "import rmi_rag\|from rmi_rag\|qdrant_client\|from app\.rag" /srv/work/repos/rmi-backend/app --include="*.py"` → 0 matches except `app/rag_client.py`.
3. Run both greps in CI (`.forgejo/workflows/`) as a hard gate. [decision]
4. Ruff `TID252`/`PLC0415`-style rules are advisory; the greps are the contract.

---

## 7. Error Handling Contract

| Failure | Raise / Return | HTTP |
|---|---|---|
| Ollama down / timeout / non-2xx | `EmbeddingError` (from `embedder.py`) | 503 |
| Qdrant down / timeout / non-2xx | `StoreError` (from `store.py`) | 503 |
| Invalid collection name / bad payload / wrong dim | `ValidationError` (API layer) | 400 |
| Missing points / no matches (infra healthy) | empty result list, `status: "ok"` | 200 |
| rmi-rag-api unreachable from backend | `RagUnavailableError` (backend client) | 503 |

Rules:
1. **Never** return empty results for infra failures — fail closed, surface 503.
2. **Never** silently degrade to hash/random fallback embeddings. `embeddings.py` already documents this ("fails LOUDLY"); keep it in `embedder.py`.
3. `EmbeddingError`/`StoreError` defined in one place (`config.py` or a tiny `errors.py` [decision]) and imported by both layers.
4. API handlers catch `EmbeddingError|StoreError` → `HTTPException(503, detail="rag infrastructure unavailable")`; `ValidationError` → 400 with field detail.
5. Backend proxy maps `RagUnavailableError` → 503 and preserves rmi-rag-api's 400 details verbatim (pass-through of `detail`).
6. Structured logging: `observability.py` logs every failure with `event="rag_error"`, `error_type`, `latency_ms` — no silent `except: pass` anywhere.

---

## 8. API Contract

### 8.1 Canonical routes (rmi-rag-api, all under `api/*`)

| Method | Path | Module | Request | Response |
|---|---|---|---|---|
| POST | `/api/v1/search` | `api/search.py` | `{query, collection?, filters?, limit?}` | `{hits: [{id, score, payload, source}], total, latency_ms}` |
| POST | `/api/v1/rag/v2/scam-check` | `api/scam_check.py` | `{name, symbol, address?, description?}` | `{probability, matched_patterns, verdict, evidence}` |
| POST | `/api/v1/ingest` | `api/ingest.py` | `{collection, items: [{id?, text, metadata}]}` | `{inserted, deduped, failed}` |
| POST | `/api/v1/investigate` | `api/investigate.py` | `{query, depth?, collections?}` | `{context, findings, citations}` |
| POST | `/api/v1/confidence` | `api/confidence.py` | `{query, hits?}` | `{score, explanation}` |
| GET | `/api/v1/stats` | `api/admin.py` | — | `{collections: {name: {points, dim, updated_at}}, embed_model, latency_p50}` |
| GET | `/health` `/ready` | `main.py` | — | 200 when embedder+store initialized |

Legacy aliases: `GET /api/v1/search?q=...` — keep during Phase 4 only for the bot's existing `research` handler, then migrate to POST and delete [decision].

### 8.2 Route-triage from current `endpoints.py` [verified list]

| Current route | Target | Action |
|---|---|---|
| `POST /api/v1/rag/confidence` | `api/confidence.py` | move |
| `POST /api/v1/rag/chunk-solidity` | — | DELETE [decision: no consumer; chunking is internal] |
| `POST /api/v1/scanner/deployer-risk` | — | DELETE [decision: scanner owns deployer risk; not RAG core] |
| `POST /api/v1/rag/cross-chain` | — | DELETE [decision] |
| `POST /api/v1/rag/image-analysis` | — | DELETE [decision: multimodal out of scope] |
| `POST /api/v1/rag/logo-check` | — | DELETE [decision] |
| `POST /api/v1/rag/investigate` | `api/investigate.py` | move |
| `GET /api/v1/rag/graph-communities` | — | DELETE [decision: graph_rag deleted] |
| `POST /api/v1/rag/watch` / `GET /api/v1/rag/watches` | — | DELETE [decision: realtime_alerts/watchlist deleted; bot alerting is separate] |
| `GET /api/v1/rag/journalism/research` | `api/investigate.py` (or keep under journalism if retained) | KEEP by default [decision] — final call by agent after verifying bot/frontend usage |
| `GET /api/v1/email/accounts` | — | DELETE [decision: not RAG; stray endpoint] |
| `GET /api/v1/search` | `api/search.py` (POST) | migrate |
| `GET /api/v1/rag/v2/scam-check` | `api/scam_check.py` (POST) | move + switch to POST |

---

## 9. Tests to update (existing suite) [verified files]

- `tests/test_search.py` → targets `retrieval.py`; drop `three_pillar_search` tests.
- `tests/test_embeddings.py` → targets `embedder.py`; assert no-fallback failure mode.
- `tests/test_qdrant_store.py`, `tests/test_qdrant.py` → targets `store.py`.
- `tests/test_endpoints.py` → targets `api/*`; update paths for moved routes.
- `tests/test_subscriber.py`, `tests/test_scanner_integration.py` → targets `ingestion/scanner.py`.
- NEW: `tests/test_import_boundaries.py` — runs the §6 grep gates (CI-friendly).
- NEW: `tests/test_rag_client.py` (backend) — respx-mocked proxy tests: 200 pass-through, 503 mapping, 400 detail pass-through.

---

## 10. Migration Sequencing (order for the 6 agents)

**Phase 1 — Proof of proxy (1 agent, backend):** create `app/rag_client.py` + rewrite `app/api/v1/rag/search.py` to proxy. Delete `app/rag_firehose/`, `app/rag_*.py` facades, `supabase_rag.py`, `app/databus/rag_*`. Add §6 CI greps. Tests: `test_rag_client.py`.
> Cutover risk: after this phase the bot path already hits rmi-rag-api. Verify manually with a bot `/research` call.

**Phase 2 — Redirect the 31 `rag_service` consumers (2 agents, backend):** swap every `app.rag_service.*` / `app.rag.*` import to `RagApiClient`. Fix `rag_scam_check.py` C1. Do NOT delete `app/rag/` or `app/rag_service/` yet (imports in tests/scripts may linger). Grep gate: 0 references to `rag_service`; `app.rag` references only inside `app/rag/` itself.

**Phase 3 — Delete the backend implementations (1 agent):** delete `app/rag/`, `app/rag_service/`. Grep gate: `grep -rn "app\.rag\b\|app\.rag_service\|from app\.rag" app/ tests/` = 0. Backend is now pure proxy.

**Phase 4 — rmi-rag module map (2 agents, parallel lanes):**
- Lane A (core): `store.py` + `retrieval.py` + `config.py`; delete `search/` package, `search_similar.py`, `search_service.py`, `splade_bm25.py`, `cross_encoder.py`, `rrf_fusion.py` (fold), `embeddings.py` (redirect), `crypto_embeddings.py` (cascade), `bge_embedders.py`, `rag_provider.py`, `rag_ingestion.py`, `embed_utils.py`, `embed_tiers.py`, `embedding_router.py`, `query_classifier.py`, `query_rewriter.py`, `legacy_supabase.py`, `metrics_api.py`, `agentic.py`, `tool.py`, `chunking.py`, `permanence.py`, `sparse_search.py`, `src/app/` (whole).
- Lane B (API+ingestion): split `endpoints.py` into `api/*` per §8.2; move `scanner_subscriber/poller` → `ingestion/scanner.py`; `pattern_extractor` → `ingestion/news.py`; `scam_detection.py` → `api/scam_check.py` support; `eval_ragas.py` → `evaluation/eval.py`; fix `embedder.py`; slim `__init__.py`; fix `import_guard.py`.
- Both lanes land on the same release train; repo must pass tests at merge.

**Phase 5 — Guard removal + cleanup:** after `grep -rnE "from app\.|import app\." src/rmi_rag/` = 0 (excluding `import_guard.py` itself), delete `import_guard.py`, remove its install call from `main.py`, delete `journalism.py` if §8.2 verdict was DELETE, delete any remaining disposition-DELETE stragglers.

**Phase 6 — Verification & ship:** full pytest, §6 grep gates, manual smoke: bot research, scanner scan with scam-check, frontend chat, `/stats` showing 3 collections (scanner/news/wallet), RAGAS eval run, then update `ARCHITECTURE.md` to point at V2.

---

## 11. Definition of Done (acceptance gates)

1. `grep -rn "rag_service\|rag_firehose\|crypto_embeddings\|splade_bm25\|three_pillar" /srv/work/repos/rmi-backend/app /srv/work/repos/rmi-rag/src` → 0 matches.
2. `grep -rnE "from (app\.|rmi_rag\.)" /srv/work/repos/rmi-rag/src/rmi_rag/{embedder,store,retrieval}.py` → 0.
3. `grep -rn "qdrant_client\|rmi_rag\|from app\.rag" /srv/work/repos/rmi-backend/app --include="*.py"` → 0 except `app/rag_client.py`.
4. All rmi-rag tests + backend proxy tests green (`pytest`, `mypy --strict`, `ruff`).
5. Live smoke: bot `/research` returns Qdrant-grounded citations; scanner scam-check returns verdict from `rmi-rag-api`; `/health` + `/ready` green.
6. `RMI_RAG_API_URL=http://rmi-rag-api:8012` documented in backend `.env.example`; compose on shared `rmi_network`.
7. This document's §3.3/§3.4 disposition tables fully executed; `git log` shows conventional commits per phase (`feat(rag):`, `refactor(rag):`, `chore(rag):`, `delete(rag):`).

---

*End of blueprint. Conflicts with the repo's `ARCHITECTURE.md` (the "as-is" doc) are expected — V2 is the "to-be" contract; ARCHITECTURE.md should be updated in Phase 6 to reference this document.*

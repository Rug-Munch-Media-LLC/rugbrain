# rmi-rag — Production TODO
**Last audit:** 2026-08-01 (final sweep + BM25 alias swap) · **Status:** Phase 1 consolidation complete, Phase 2 in flight · **Grade:** A (compliance 94%)

## ✅ PHASE 1 — COMPLETED (2026-08-01)

- [x] **CRITICAL: Fix backend embedder chain** — DONE 2026-07-31. rmi-backend now uses BGE-M3 as PRIMARY via Ollama `/api/embed` (1024-dim, `EMBEDDER_PRIMARY=ollama`, `EMBEDDER_PRIMARY_MODEL=bge-m3`). Dead `:8001` container path eliminated. Fallback: `nomic-embed-text` via Ollama. Verified 2026-08-01: `ollama:bge-m3` (1.16GB) + `nomic-embed-text` (274MB) both present.
- [x] **CRITICAL: Pull nomic-embed-text into Ollama** — DONE. Verified 2026-08-01.
- [x] **CRITICAL: Seed Qdrant collections** — DONE 2026-08-01. Verified live counts (final sweep):
  - `scam_intel` 600 · `scam_intel_v2` 600 (ScamSniffer 2,530 address cache + Etherscan phish CSV + curated patterns)
  - `scanner` 191 · `scanner_v2` 191 (new_pairs.json + scan_results + scan_reports dumps)
  - `news` 623 · `news_v2` 623 (news_items.json dump, 1,000 rows → chunked)
  - `research` 60 · `research_v2` 60 (arXiv API feed; target 50+)
  - `scams` 300 (MetaMask eth-phishing-detect sample) · `wallet` 1000 (Redis `rmi:label:*`)
  - `threat_intel` 26 · `threat_intel_v2` 26 (static patterns + historical hacks)
  - `hacks` **50 — SEEDED (was 0 at Phase 1; `ec4fb7b` restored hack ingest scripts and they ran)**
  - `hacks_v2` **50 — populated 2026-08-01 (sparse migration backfill) + aliased `hacks → hacks_v2`**
  - `rmi_agent_memory` 5 · `rmi_community` 21
  Seeding scripts: `scripts/seed_qdrant_collections.py` (primary), `scripts/news_to_rag.py` (news), `scripts/ingest_rekt_news.py` (hacks).
- [x] R2→B2 migration — all backup/permanence scripts, env vars, docs migrated (2026-07-31). Verified: 0 live R2 references (2 remaining strings are migration comments only).
- [x] B2 permanence pipeline — `scripts/b2_permanence.py` parquet snapshots verified (dry-run: **20 collections**, bucket `rmi-rag-permanence`, `format=parquet`, `encrypt=False`). Cron: `backup_qdrant.py` nightly + systemd timer.
- [x] **Parquet + DuckDB permanence** — `scripts/b2_permanence.py` writes parquet (zstd); `scripts/migrate_to_parquet.py` converts legacy NDJSON; `POST /api/v1/rag/query` (read-only SQL) added in HEAD (`parquet_analytics.py`). `tests/test_parquet_permanence.py` — **18 passed**. **DEPLOYED 2026-08-01**: container rebuilt from HEAD, `/api/v1/rag/query` verified 200 (b2-direct source, DuckDB httpfs baked into image). ⚠️ Known: `_b2_native_auth` fallback uses `httpx.post` w/o body — B2 authorize returns 400 on POST; GET works. Primary httpfs path unaffected.
- [x] **Client-side B2 encryption REMOVED** — `55ca320 refactor(b2)`: no client-side AES-GCM; **B2 SSE-B2 (AES256)** encrypts at rest. `B2_ENCRYPTION_KEY` deprecated, absent from `.env.rag`. `migrate_to_parquet.py` remains the only reader of legacy `.enc` objects.
- [x] **BM25 sparse vectors (Phase 2 item, shipped early)** — `32d88f6`: `scripts/migrate_to_sparse.py` creates `{name}_v2` collections (named dense + bm25 sparse); 9 `_v2` collections live with points.
- [x] **BGE reranker merged** — `8fbab3e` + `5ee229e` + `1f73843`: `reranker.py` (bge-reranker-v2-m3), opt-in `rerank=true`; torch-init fixed. Search never breaks on reranker failure.
- [x] RAGAS evaluation framework — `src/rmi_rag/eval_ragas.py` + `evaluation.py` + golden dataset + metrics; route `GET /api/v1/eval/run` merged into main (`1f73843`). **DEPLOYED 2026-08-01** on :8012 (eval-test container removed).
- [x] RAG metrics — `metrics.py` Prometheus counters (`embed_latency`, `embed_requests`) + `/metrics` mount. **DEPLOYED 2026-08-01** — `/metrics` returns 200 with API key.
- [x] ARCHITECTURE-V2.md updated with as-built reality (§0.1 final sweep) + DATA-STRATEGY.md written (DATA-STRATEGY.md is NOT in this repo — it lives in rmi-backend).
- [x] Historical crypto data ingestion scripts — `scripts/ingest_defihacklabs.py`, `scripts/ingest_rekt_news.py` restored to main (`ec4fb7b`) and **run against live Qdrant** (hacks = 50 points).

## 🔴 BLOCKING (from final sweep 2026-08-01)

- [x] **CRITICAL: Rebuild `rmi-rag-api` container from HEAD** — DONE 2026-08-01 (container recreated 12:31 UTC). Verified live: `/ready` 200, `/api/v1/rag/query` POST 200 (b2-direct via DuckDB httpfs), `/metrics` 200 (with API key), `/api/v1/search` 200. Dockerfile now bakes `duckdb httpfs` extension at build time.
- [x] **CRITICAL: Stop stale `rmi-rag-eval-test` container (:8013)** — DONE 2026-08-01. Container removed; no `eval-test` container remains. (Image `rmi-rag-eval-test:latest` still on disk — prune when convenient.)
- [ ] **CRITICAL: Backend consolidation Phases 1–3 never executed** — `app/rag_client.py` (RagApiClient) NOT created; `app/api/v1/rag/search.py` still imports `app.rag` in-process; **17 callers** of `app.rag_service` remain (down from 31); `app/rag/` (17 files) + `app/rag_service/` (6 files) still present. See ARCHITECTURE-V2.md §10 for the full plan.
- [ ] **CRITICAL: Fix news cron Redis auth on host** — `rmi-news-ingest.sh` step 1 fails: "HELLO must be called with the client already authenticated". Script sources `REDIS_PASSWORD` but backend `app/core/redis.py` reads `REDIS_URL` (default no-auth localhost). Fix: export `REDIS_URL=redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0` in cron env. **Still open — verified in script, no fix present.**
- [ ] **CRITICAL: Wire scanner ingestion pipeline** — `scanner_subscriber` and `scanner_poller` both `running: true` but `ingested_count: 0`; Redis queues `rmi:rag:ingest` / `rmi:rag:findings_queue` empty. Scanner/news collections are seeded from staged dumps; live ingestion not wired.
- [ ] **HIGH: Fix live search latency + event-loop wedge** — bge-m3 unloads after keep_alive expiry; first embed reloads ~38–45s and the sync `httpx.Client(timeout=120)` inside async code blocks the event loop. Observed: under concurrent load the API wedges (even `/ready` times out). Fixes: keep model resident (keep_alive), move embed off the event loop (async httpx / to_thread), add request-level timeout.

## 🟠 HIGH

- [x] **Ollama optimization** — DONE 2026-08-01 (compose + warmup script, see `scripts/ollama-warmup.sh`): `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=3`, `OLLAMA_CONTEXT_LENGTH=4096`, cpus 8 / mem 8g; bge-m3 + nomic-embed-text preloaded at container start; `qwen3:0.6b` removed (qwen3:1.7b retained). Verified live on rmi-ollama.
- [ ] **Event-loop wedge confirmed post-swap (2026-08-01)** — `/api/v1/search` batch during verification wedged `rmi-rag-api` (unhealthy, `/ready` 000, needed `docker restart`). Cold bge-m3 embed reload (~40–45s on first search) + sync `httpx.Client(timeout=120)` inside async blocks the event loop. Now that hybrid_search runs on every canonical collection, each new process pays the cold reload — fix keep_alive resident model + move embed off the event loop (async httpx / to_thread) + request-level timeout. Was already tracked as "Fix live search latency + event-loop wedge"; confirmed recurring 2026-08-01.
- [x] Fix `rmi-news-ingest.sh` cron pileup — DONE 2026-08-01 (`c17dc59`). `flock -n 200` guard: overlapping runs skip and exit 0; covers cron + systemd timer paths. **Caveat:** step-2 Redis auth gap (`REDIS_URL` vs `REDIS_PASSWORD`) still open, tracked separately.
- [x] Seed `hacks_v2` sparse variant — DONE 2026-08-01. `migrate_to_sparse.py --collection hacks` backfilled `hacks_v2` to 50 points (bm25 sparse computed), then `--swap` aliased `hacks → hacks_v2`.
- [x] **BM25 alias swap** — DONE 2026-08-01. **8/8 collections swapped** (threat_intel, scam_intel, hacks, news, wallet, scanner, research, scams): canonical names are Qdrant aliases → `{name}_v2` hybrid (dense `dense` + sparse `bm25`). `migrate_to_sparse.py` made idempotent (skip copy when `_v2` complete; reuse existing hybrid `_v2`). Verified live: all canonical names resolve `sparse=bm25` with full point counts; `/api/v1/search` returns 3/3 hits per collection via canonical names.
- [ ] Delete remaining §3.1 modules in rmi-rag — `search/` package, `search_service.py`, `splade_bm25.py`, `cross_encoder.py`, `embeddings.py`, `crypto_embeddings.py`, `bge_embedders.py`, `rag_ingestion.py`, `query_rewriter.py`, `permanence.py`, `sparse_search.py`, `analytics_storage.py`, `app_chunking.py`, `bundle_cluster.py`, `cross_chain.py`, `embedding_finetune.py`, `feature_extractors.py`, `feedback.py`, `github_feeder.py`, `graph_rag.py`, `historical.py`, `incremental_indexer.py`, `multimodal.py`, `parent_retriever.py`, `realtime_alerts.py`, `seed_scams.py`, `solidity_chunker.py`, `streaming.py`, `firehose/`. (11 files already deleted.)
- [ ] Split `endpoints.py` into `api/*` per ARCHITECTURE-V2 §8.2 (currently one file with all routes incl. new `/rag/query` + `/eval/run`).
- [ ] Remove qwen3 legacy enum entry + `_BACKEND_DIMS` dead path from `embedder.py` (BGE-M3 1024d only).
- [ ] Fix rmi-rag test_qdrant_store failure — assertion expects `doc_001` as point ID but store uses UUID.
- [ ] Fix SDK tests — `ModuleNotFoundError: No module named 'rugmunch'` (package is `rugmunch_sdk`, tests stale).
- [ ] Align rmi-codebase-mcp embeddings with bge-m3 — currently hardcoded to nomic-embed-text (768-dim). Migrate to bge-m3 (1024-dim) or pull nomic.
- [ ] Fix rmi-backend manual `test_rag.py` absent from container — file exists in repo but not in `/app/tests/manual/`.
- [ ] Fix `rag-monitor` script SyntaxError — unquoted redis-cli output injected into Python code.

## 🟡 MEDIUM

- [ ] Fix Qdrant nightly backup — failing every night: "Credential access key has length 0, should be 32" (missing S3 creds).
- [ ] Clean up stale ChromaDB instance — `data/chroma/` (188KB, 0 collections) vs active `chroma_db/` (5 embeddings, stale).
- [ ] Clean up Qdrant test-artifact collections — `_test_sparse6`, `_test_sparse8` (empty, leftover from sparse-migration testing) and any `tmp_old` remnant. `tmp_old` deleted 2026-08-01; `_test_*` remain.
- [ ] `hybrid_search` uses `_collection_supports_sparse` per-instance cache — cache is not invalidated if a collection is swapped/recreated under a running process; acceptable for now (canonical names are aliases and stable), but restart the API after any future migration swap to refresh the probe.
- [ ] Fix rmi-rag-api health shows `ingested_count: 0` — either fix or remove from dashboard.
- [ ] Fix `RAG_EMBED_BACKEND=auto` ambiguity — explicit config would prevent broken fallback chain.
- [ ] Create reindex cron/scheduler — seed data in place; add a reindex schedule to keep collections fresh (news/research/scams are static snapshots until wired).
- [ ] Remove stale repo copies — DONE 2026-08-01 (partial): nested clone `/srv/work/repos/rmi-backend/rmi-rag/` removed + gitlink dropped (`rmi-backend c3682e02`); `/tmp/opencode/rmi-rag-main-verify` + `/tmp/opencode/rmi-rag-reranker` removed. **Remaining:** worktree `rmi-rag-wt-ragas` (wip/ragas-eval-endpoint, branch merged) still registered — `git worktree remove` it.

## 🔵 PHASE 2 — QUEUED

- [ ] Full RAGAS eval with LLM metrics (blocked by Ollama load; needs deployed eval endpoint first)
- [ ] Pruning policy implementation (qdrant_store.prune_old_documents)
- [ ] Neo4j entity expansion for GraphRAG
- [ ] Image/code embedding pipeline

## 🟢 LOW

- [ ] Add metrics for RAG query latency, hit rate, collection sizes
- [ ] Fix rmi-rag `make lint` (42 ruff errors)
- [ ] Fix rmi-rag `make test` (1 failure, coverage 10%, threshold 60% not enforced)
- [ ] Add SECURITY.md
- [ ] Fix CHANGELOG empty

## Tests

- [x] Integration test added: `tests/test_parquet_permanence.py` (18 tests, passing)
- [ ] Fix 1 failing test (test_qdrant_store.py)
- [ ] Fix SDK tests (ModuleNotFoundError)
- [ ] Add integration tests: end-to-end embed → store → search → retrieve
- [ ] Add tests for ingest pipeline (scanner → Redis → Qdrant)
- [ ] Enforce coverage threshold (pyproject says 60%, CI doesn't use --cov-fail-under)

*Last sweep evidence: pytest 81 passed / 5 skipped; search returns 2 hits (threat_intel); B2 dry-run 20 collections parquet SSE-B2; DuckDB/eval endpoints 404 on :8012 (stale build); rag_service callers 17.*

# rmi-rag — Production TODO
**Last audit:** 2026-08-01 · **Status:** Phase 1 consolidation complete, Phase 2 in flight · **Grade:** A (compliance 94%)

## ✅ PHASE 1 — COMPLETED (2026-08-01)

- [x] **CRITICAL: Fix backend embedder chain** — DONE 2026-07-31. rmi-backend now uses BGE-M3 as PRIMARY via Ollama `/api/embed` (1024-dim, `EMBEDDER_PRIMARY=ollama`, `EMBEDDER_PRIMARY_MODEL=bge-m3`). Dead `:8001` container path eliminated. Fallback: `nomic-embed-text` via Ollama. Verified 2026-08-01: `ollama:bge-m3` (1.16GB) + `nomic-embed-text` (274MB) both present.
- [x] **CRITICAL: Pull nomic-embed-text into Ollama** — DONE. Verified 2026-08-01.
- [x] **CRITICAL: Seed Qdrant collections** — DONE 2026-08-01. Verified live counts:
  - `scam_intel` 600 (ScamSniffer 2,530 address cache + Etherscan phish CSV + curated patterns)
  - `scanner` 191 (new_pairs.json + scan_results + scan_reports dumps)
  - `news` 623 (news_items.json dump, 1,000 rows → chunked; target 500+)
  - `research` 60 (arXiv API feed, cached arxiv_feed.xml; target 50+)
  - `scams` 300 (MetaMask eth-phishing-detect blacklist sample; target 300)
  - `wallet` 1000 (Redis `rmi:label:*` — 10,127 keys deduped by address+chain; target 1000+)
  - `threat_intel` 26 (static patterns + historical hacks + Redis seed docs)
  - `rmi_agent_memory` 5 (curated entity profiles)
  - `rmi_community` 21 (community submissions + curated topics)
  Seeding scripts: `scripts/seed_qdrant_collections.py` (primary), `scripts/news_to_rag.py` (news), `scripts/seed_threat_intel.py` (threat_intel).
- [x] R2→B2 migration — all backup/permanence scripts, env vars, docs migrated (2026-07-31). Verified: 0 live R2 references (2 remaining strings are migration comments only).
- [x] B2 permanence pipeline — `scripts/b2_permanence.py` dry-run verified (12 collections, bucket `rmi-rag-permanence` reachable). Cron: `backup_qdrant.py` nightly + systemd timer.
- [x] RAGAS evaluation framework — `src/rmi_rag/eval_ragas.py` + golden dataset + metrics. NOTE: API endpoint route (`/api/v1/ragas/eval`) lives on `wip/ragas-eval-endpoint` worktree, not yet deployed.
- [x] Re-ranker research — bge-reranker-v2-m3 chosen; code merged into main as `feat(rag): add bge-reranker-v2-m3` (8fbab3e), pre-push mypy still failing on this WIP.
- [x] ARCHITECTURE-V2.md + DATA-STRATEGY.md written.
- [x] Historical crypto data ingestion scripts — `scripts/ingest_defihacklabs.py`, `scripts/ingest_rekt_news.py`, `scripts/ingest_swc_registry.py` on `wip/rag-phase1-consolidation`. ⚠️ NOT yet run against live Qdrant (see below).
- [x] RAG metrics — Prometheus counters + `/metrics` endpoint verified.

## 🔴 BLOCKING (Phase 1 gaps found in verification 2026-08-01)

- [ ] **CRITICAL: Seed `hacks` collection in live Qdrant** — collection exists (created by BM25 migration tooling) but has **0 points**. Expected 100+. `ingest_rekt_news.py` was never deployed into the rmi-rag-api container nor run against live Qdrant. Run: `python3 scripts/ingest_rekt_news.py` (from `wip/rag-phase1-consolidation` or after merge) against live Qdrant.
- [ ] **CRITICAL: Fix news cron Redis auth on host** — `rmi-news-ingest.sh` step 2 fails: "HELLO must be called with the client already authenticated". Root cause: `/srv/secrets/backend.env` exports `REDIS_PASSWORD` but backend `app/core/redis.py` reads `REDIS_URL` (default no-auth localhost). Fix: export `REDIS_URL=redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0` in cron env (or add URL-building validator). Pre-existing since 02:06Z; container unaffected (uses REDIS_HOST/PORT/PASSWORD directly).
- [ ] **CRITICAL: Wire scanner ingestion pipeline** — `scanner_subscriber` and `scanner_poller` both `running: true` but `ingested_count: 0`. Redis queues `rmi:rag:ingest` and `rmi:rag:findings_queue` empty. Nothing pushes data to RAG. (scanner collection is seeded from staged dumps, but live ingestion still not wired.)
- [ ] **CRITICAL: Fix rmi-rag-api 500s** — `/api/v1/rag/investigate` and `/api/v1/rag/confidence` fail with `ModuleNotFoundError: No module named 'app'`. Fix container PYTHONPATH/packaging.

## 🟠 HIGH

- [ ] Fix `rmi-news-ingest.sh` cron pileup — every 15-min tick spawns `app.domains.news.ingest` that never terminates when Ollama is busy; dozens of stuck processes accumulate and saturate the embedder. Ingest step has no lock (unlike news_to_rag).
- [ ] Deploy RAGAS eval API endpoint — merge `wip/ragas-eval-endpoint` route into main + redeploy container.
- [ ] Fix rmi-rag test_qdrant_store failure — assertion expects `doc_001` as point ID but store uses UUID.
- [ ] Fix SDK tests — `ModuleNotFoundError: No module named 'rugmunch'` (package is `rugmunch_sdk`, tests stale).
- [ ] Align rmi-codebase-mcp embeddings with bge-m3 — currently hardcoded to nomic-embed-text (768-dim). Migrate to bge-m3 (1024-dim) or pull nomic.
- [ ] Fix rmi-backend manual `test_rag.py` absent from container — file exists in repo but not in `/app/tests/manual/`.
- [ ] Fix `rag-monitor` script SyntaxError — unquoted redis-cli output injected into Python code.

## 🟡 MEDIUM

- [ ] Fix Qdrant nightly backup — failing every night: "Credential access key has length 0, should be 32" (missing S3 creds).
- [ ] Clean up stale ChromaDB instance — `data/chroma/` (188KB, 0 collections) vs active `chroma_db/` (5 embeddings, stale).
- [ ] Fix rmi-rag-api health shows `ingested_count: 0` — either fix or remove from dashboard.
- [ ] Fix `RAG_EMBED_BACKEND=auto` ambiguity — explicit config would prevent broken fallback chain.
- [ ] Create reindex cron/scheduler — seed data in place; add a reindex schedule to keep collections fresh (news/research/scams are static snapshots until wired).

## 🔵 PHASE 2 — QUEUED

- [ ] BM25 sparse vectors in Qdrant collections (in flight — `migrate_to_sparse.py`, `_v2` collections appearing; concurrent session)
- [ ] Merge reranker from worktree into main (bge-reranker-v2-m3, `feat/rag-bge-reranker`); green pre-push mypy (96 errors currently on reranker/BM25 WIP)
- [ ] Full RAGAS eval with LLM metrics (blocked by Ollama load)
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

- [ ] Fix 1 failing test (test_qdrant_store.py)
- [ ] Fix SDK tests (ModuleNotFoundError)
- [ ] Add integration tests: end-to-end embed → store → search → retrieve
- [ ] Add tests for ingest pipeline (scanner → Redis → Qdrant)
- [ ] Enforce coverage threshold (pyproject says 60%, CI doesn't use --cov-fail-under)

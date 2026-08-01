# rmi-rag — Production TODO
**Last audit:** 2026-08-01 · **Status:** All collections seeded · **Grade:** C

## 🔴 BLOCKING

- [x] **CRITICAL: Fix backend embedder chain** — DONE 2026-07-31. rmi-backend now uses BGE-M3 as PRIMARY via Ollama `/api/embed` (1024-dim, `EMBEDDER_PRIMARY=ollama`, `EMBEDDER_PRIMARY_MODEL=bge-m3`). Dead `:8001` container path eliminated. Fallback: `nomic-embed-text` via Ollama. Verify: `/api/v1/rag/v2/stats` shows `backend: ollama:bge-m3`.
- [x] **CRITICAL: Pull nomic-embed-text into Ollama** — DONE. `nomic-embed-text:latest` (274MB) present in rmi-ollama. bge-m3 (1.2GB) also present.
- [x] **CRITICAL: Seed Qdrant collections** — DONE 2026-08-01. All 9 collections exist at 1024-dim Cosine and are populated:
  - `scam_intel` 600 (ScamSniffer 2,530 address cache + Etherscan phish CSV + curated patterns)
  - `scanner` 191 (new_pairs.json + scan_results + scan_reports dumps)
  - `news` 623 (news_items.json dump, 1,000 rows → chunked; target 500+)
  - `research` 60 (arXiv API feed, cached arxiv_feed.xml; target 50+)
  - `scams` 300 (MetaMask eth-phishing-detect blacklist sample; target 300)
  - `wallet` 1000 (Redis `rmi:label:*` — 10,127 keys deduped by address+chain; target 1000+)
  - `threat_intel` 26 (unchanged, already done)
  - `rmi_agent_memory` 5 (curated entity profiles)
  - `rmi_community` 21 (community submissions + curated topics)
  Seeding scripts: `scripts/seed_qdrant_collections.py` (primary + follow-up runs), `scripts/news_to_rag.py` (superseded by dump loader), `scripts/seed_threat_intel.py` (threat_intel).
- [ ] **CRITICAL: Wire scanner ingestion pipeline** — `scanner_subscriber` and `scanner_poller` both `running: true` but `ingested_count: 0`. Redis queues `rmi:rag:ingest` and `rmi:rag:findings_queue` empty. Nothing pushes data to RAG. (scanner collection is seeded from staged dumps, but live ingestion still not wired.)
- [ ] **CRITICAL: Fix rmi-rag-api 500s** — `/api/v1/rag/investigate` and `/api/v1/rag/confidence` fail with `ModuleNotFoundError: No module named 'app'`. Fix container PYTHONPATH/packaging.

## 🟠 HIGH

- [x] Ingest seed data into threat_intel collection — DONE 2026-08-01: 26 docs (static patterns + historical hacks + Redis seed docs, bge-m3 1024-dim).
- [ ] Fix `rmi-news-ingest.sh` cron pileup — every 15-min tick spawns `app.domains.news.ingest` that never terminates when Ollama is busy; dozens of stuck processes accumulate and saturate the embedder. Ingest step has no lock (unlike news_to_rag). Also Redis auth failure in cron env: "HELLO must be called with the client already authenticated" (missing REDIS_PASSWORD in /srv/secrets/backend.env source path).
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

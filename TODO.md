# rmi-rag — Production TODO
**Last audit:** 2026-07-31 · **Status:** All vector stores empty · **Grade:** D+

## 🔴 BLOCKING

- [x] **CRITICAL: Fix backend embedder chain** — DONE 2026-07-31. rmi-backend now uses BGE-M3 as PRIMARY via Ollama `/api/embed` (1024-dim, `EMBEDDER_PRIMARY=ollama`, `EMBEDDER_PRIMARY_MODEL=bge-m3`). Dead `:8001` container path eliminated. Fallback: `nomic-embed-text` via Ollama. Verify: `/api/v1/rag/v2/stats` shows `backend: ollama:bge-m3`.
- [x] **CRITICAL: Pull nomic-embed-text into Ollama** — DONE. `nomic-embed-text:latest` (274MB) present in rmi-ollama. bge-m3 (1.2GB) also present.
- [ ] **CRITICAL: Recreate Qdrant collections** — configured collections (`scanner`, `wallet`, `news`, `research`, `scams`) DON'T EXIST. Only `rmi_agent_memory`, `rmi_community`, `threat_intel` exist — all 0 points. Create at 1024-dim (bge-m3 dimension).
- [ ] **CRITICAL: Wire scanner ingestion pipeline** — `scanner_subscriber` and `scanner_poller` both `running: true` but `ingested_count: 0`. Redis queues `rmi:rag:ingest` and `rmi:rag:findings_queue` empty. Nothing pushes data to RAG.
- [ ] **CRITICAL: Fix rmi-rag-api 500s** — `/api/v1/rag/investigate` and `/api/v1/rag/confidence` fail with `ModuleNotFoundError: No module named 'app'`. Fix container PYTHONPATH/packaging.

## 🟠 HIGH

- [ ] Ingest seed data into threat_intel collection — 4 seed docs exist in Redis. Expand with real scam patterns.
- [ ] Fix rmi-rag test_qdrant_store failure — assertion expects `doc_001` as point ID but store uses UUID.
- [ ] Fix SDK tests — `ModuleNotFoundError: No module named 'rugmunch'` (package is `rugmunch_sdk`, tests stale).
- [ ] Align rmi-codebase-mcp embeddings with bge-m3 — currently hardcoded to nomic-embed-text (768-dim). Migrate to bge-m3 (1024-dim) or pull nomic.
- [ ] Fix rmi-backend manual `test_rag.py` absent from container — file exists in repo but not in `/app/tests/manual/`.
- [ ] Fix `rag-monitor` script SyntaxError — unquoted redis-cli output injected into Python code.

## 🟡 MEDIUM

- [ ] Fix Qdrant nightly backup — failing every night: "Credential access key has length 0, should be 32" (missing S3 creds).
- [ ] Clean up stale ChromaDB instance — `data/chroma/` (188KB, 0 collections) vs active `chroma_db/` (5 embeddings, stale).
- [ ] Update TODO.md — "Qdrant is down" flagged Blocked but Qdrant is up (just empty).
- [ ] Fix rmi-rag-api health shows `ingested_count: 0` — either fix or remove from dashboard.
- [ ] Fix `RAG_EMBED_BACKEND=auto` ambiguity — explicit config would prevent broken fallback chain.
- [ ] Create reindex cron/scheduler — all stores empty, no automated reindex exists.

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

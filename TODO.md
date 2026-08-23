# rugbrain — Production TODO
**Last audit:** 2026-08-20 (full system audit) · **Status:** Models live, re-embedding pending, ingestion broken · **Grade:** C (compliance 65%)

## ✅ COMPLETED (2026-08-19/20)

- [x] **Reranker deployed**: Qwen3-Reranker-4B Q6_K live on :8003 (llama.cpp). 8B models deleted (39GB freed). Verified: rerank score 0.9978 vs 1.1e-04.
- [x] **Embedder deployed**: Qwen3-Embedding-4B Q8_0 live on :8004 (llama.cpp). 2560-dim, normalized. Verified: cos(relevant)=0.708 vs cos(irrelevant)=0.023.
- [x] **Qdrant v2 stood up**: `rmi-qdrant-v2` on :6334, 46 collections at 2560-dim (named `dense`, Cosine).
- [x] **llama.cpp Qwen3 embedder/BGE-M3 fully removed**: No process, container, or port :8001 exists. Legacy 1024-dim collections retained read-only on :6333.
- [x] **rugbrain-api container rebuilt** from fbb8d78 with `RERANKER_URL=http://172.22.0.1:8003`, `QWEN_EMBED_URL=http://172.22.0.1:8004/v1`. Healthy.
- [x] **BM25 sparse vectors migrated**: 8/8 canonical collections swapped to `*_v2` hybrid (dense + bm25 sparse) via aliases.
- [x] **RAGAS eval framework deployed**: `/api/v1/eval/run` + Prometheus metrics (`embed_latency`, `embed_requests`) on :8012.
- [x] **Historical crypto ingestion scripts restored**: `ingest_defihacklabs.py`, `ingest_rekt_news.py` ran → `hacks_v2` = 50 pts.

## 🔴 BLOCKING (from 2026-08-20 audit)

- [ ] **CRITICAL: Add instruction prefix to embedder client** — `src/rugbrain/embeddings.py` `_int_embed()` does NOT prepend `"Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {query}"` to queries. Qwen3-Embedding-4B loses 1-5% retrieval quality without it. **Must fix BEFORE re-embedding.**
- [ ] **CRITICAL: Complete Qdrant v2 re-embedding** — Only `news_v2` (352 pts) has data; 45 other v2 collections at 0 points. Run `scripts/migrate_embeddings_v2.py` overnight for all 183,940 legacy points. Checkpoint per collection.
- [ ] **CRITICAL: Fix news cron Redis auth** — `rmi-news-ingest.sh` step 2 fails: "HELLO must be called with the client already authenticated". Script sources `REDIS_PASSWORD` but `app/core/redis.py` reads `REDIS_URL`. Export `REDIS_URL=redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0` in cron env.
- [ ] **CRITICAL: Wire scanner ingestion pipeline** — `scanner_subscriber` and `scanner_poller` both `running: true` but `ingested_count: 0`; Redis queues `rmi:rag:ingest` / `rmi:rag:findings_queue` empty. Live ingestion not wired.
- [ ] **CRITICAL: Update outdated ingestion scripts** — `scripts/ingest_threats.py` uses `sentence_transformers` (1024-dim), connects to old Qdrant :6333, collection `rag_threats`. Many scripts in `scripts/` same. Rewrite to use `rugbrain.embeddings.get_embedding` (Qwen3 2560-dim) and Qdrant v2.
- [ ] **CRITICAL: Fix event-loop wedge on cold embed** — qwen3-embedding-4b unloads after keep_alive expiry; first embed reloads ~38-45s and sync `httpx.Client(timeout=120)` inside async blocks event loop. Fixes: keep model resident, move embed off event loop (async httpx / `to_thread`), add request-level timeout.

## 🟠 HIGH

- [ ] **Fix rugbrain test_qdrant_store failure** — Assertion expects `doc_001` as point ID but store uses UUID v5.
- [ ] **Fix SDK tests** — `ModuleNotFoundError: No module named 'rugmunch'` (package is `rugmunch_sdk`, tests stale).
- [ ] **Align rmi-codebase-mcp embeddings** — Currently hardcoded to nomic-embed-text (768-dim). Migrate to qwen3-embedding-4b (2560-dim) or pull nomic.
- [ ] **Fix rmi-backend manual `test_rag.py` absent from container** — File exists in repo but not in `/app/tests/manual/`.
- [ ] **Fix `rag-monitor` script SyntaxError** — Unquoted redis-cli output injected into Python code.
- [ ] **Delete remaining §3.1 modules in rugbrain** — `search/` package, `search_service.py`, `splade_bm25.py`, `cross_encoder.py`, `embeddings.py`, `crypto_embeddings.py`, `bge_embedders.py`, `rag_ingestion.py`, `query_rewriter.py`, `permanence.py`, `sparse_search.py`, `analytics_storage.py`, `app_chunking.py`, `bundle_cluster.py`, `cross_chain.py`, `embedding_finetune.py`, `feature_extractors.py`, `feedback.py`, `github_feeder.py`, `historical.py`, `incremental_indexer.py`, `multimodal.py`, `parent_retriever.py`, `realtime_alerts.py`, `seed_scams.py`, `solidity_chunker.py`, `streaming.py`, `firehose/`.
- [ ] **Split `endpoints.py` into `api/*`** per ARCHITECTURE-V2 §8.2 (currently one file with all routes incl. new `/rag/query` + `/eval/run`).
- [ ] **Remove qwen3 legacy enum entry + `_BACKEND_DIMS` dead path** from `embedder.py` (BGE-M3 1024d only, now removed).
- [ ] **Fix Qdrant nightly backup** — Failing every night: "Credential access key has length 0, should be 32" (missing S3 creds).
- [ ] **Clean up stale ChromaDB instance** — `data/chroma/` (188KB, 0 collections) vs active `chroma_db/` (5 embeddings, stale).
- [ ] **Clean up Qdrant test-artifact collections** — `_test_sparse6`, `_test_sparse8` (empty, leftover from sparse-migration testing).
- [ ] **Fix `RAG_EMBED_BACKEND=auto` ambiguity** — Explicit config would prevent broken fallback chain. Set `RAG_EMBED_BACKEND=qwen3` in production.
- [ ] **Create reindex cron/scheduler** — Seed data in place; add a reindex schedule to keep collections fresh (news/research/scams are static snapshots until wired).
- [ ] **Remove stale repo copies** — Worktree `rugbrain-wt-ragas` (wip/ragas-eval-endpoint, branch merged) still registered — `git worktree remove` it.

## 🟡 MEDIUM

- [ ] **Fix rugbrain-api health shows `ingested_count: 0`** — Either fix or remove from dashboard.
- [ ] **Fix `hybrid_search` per-instance sparse cache** — Cache not invalidated if collection swapped/recreated under running process; acceptable for now (canonical names are aliases and stable), but restart API after any future migration swap.
- [ ] **Fix rugbrain `make lint`** (42 ruff errors).
- [ ] **Fix rugbrain `make test`** (1 failure, coverage 10%, threshold 60% not enforced).
- [ ] **Add SECURITY.md**.
- [ ] **Fix CHANGELOG empty**.

## 🟢 PHASE 2 — QUEUED

- [ ] Full RAGAS eval with LLM metrics (blocked by Ollama load; needs deployed eval endpoint first).
- [ ] Pruning policy implementation (`qdrant_store.prune_old_documents`).
- [ ] Neo4j entity expansion for GraphRAG.
- [ ] Image/code embedding pipeline.

## 🔵 LOW

- [ ] Add metrics for RAG query latency, hit rate, collection sizes.
- [ ] Fix rugbrain `make lint` (42 ruff errors).
- [ ] Fix rugbrain `make test` (1 failure, coverage 10%, threshold 60% not enforced).
- [ ] Add SECURITY.md.
- [ ] Fix CHANGELOG empty.
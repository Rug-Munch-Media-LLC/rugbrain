# RugBrain RAG System Audit Findings
**Date**: 2026-10-14  
**Worktree**: `/srv/work/worktrees/code-review/rugbrain`  
**Scope**: API, MCP surface, ingest pipeline, collections/storage, code quality  

## Severity-Ranked Findings Table

| Severity | Subsystem | File Path | Evidence (one line) |
|----------|-----------|-----------|---------------------|
| **BLOCKER** | Ingest Pipeline | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/ingest_sources/defihacklabs.py` | `DEFAULT_COLLECTION = "threat_intel_v2"` (base name) while source writes 4096 vectors; lane resolver fixes write path but Redis content keys use base name causing silent keyword lane degradation |
| **BLOCKER** | Redis Content Lane | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/search/lane_targets.py` (lines 97-109) | `redis_content_collection()` deliberately returns base name; if resolved would break keyword/BM25 lane (empty bodies) - documented as intentional but creates dual-write complexity |
| **MAJOR** | API Auth | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/endpoints.py` (rate limit check) | Ingest route uses `search` tier limits (3600/h) but should use separate `ingest` tier; allows ingest flooding to exhaust search quota |
| **MAJOR** | MCP Tool Coverage | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/rugbrain_mcp/server.py` (lines 115-467) | Missing MCP tools for: `market_brief`, `social_sentiment`, `scam_radar` (only available as async ToolResult variants); sync clients break |
| **MAJOR** | Embedder Fallback | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/routes/core.py` (lines 49-89) | Local fallback (`http://127.0.0.1:8004/v1`) has 30s timeout vs remote 20s; wedged local service blocks caller budget |
| **MAJOR** | Lane Table Maintenance | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/qdrant_store/lane_table.py` (lines 77-106) | `_declared_twins()` docstring contains duplicate justification; 21 owned names listed but hard to audit vs live Qdrant |
| **MINOR** | API Documentation | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/routes/ingest.py` (line 28) | `IngestDocument.source` default `"api"` lacks validation; arbitrary values stored as-is |
| **MINOR** | Search Boost | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/routes/search_pkg/multi.py` (lines 123-126) | Priority boost hardcoded for only 2 collections; should be configurable per-collection |
| **MINOR** | Qdrant Client Reuse | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/qdrant_store/store_write.py` (line 20) | `_client: httpx.AsyncClient | None` recreated per operation; no connection pooling |
| **MINOR** | Error Detail Leak | `/srv/work/worktrees/code-review/rugbrain/src/rugbrain/rugbrain_mcp/server.py` (lines 106-107) | MCP error detail leaks internal RugBrain path/status; should return generic message |

## What Remains Per Subsystem

### API Surface
- **Done**: Health check, embedding passthrough, ingest endpoint with rate limiting, search endpoints with RRF fusion
- **Remaining**: 
  - Add `ingest` rate limit tier separate from `search` (prevent ingest flooding)
  - Add source validation to `IngestDocument.source` enum
  - Add OpenAPI tags for all 60+ endpoints (currently only partial)
  - Implement bulk ingest endpoint with chunking optimization
  - Add Prometheus metrics for request latency/error rates

### MCP Surface
- **Done**: 13 tools covering search, analyze, scam-check, wallet-profile, alerts, news, sentiment, vector-landscape, health
- **Remaining**:
  - Convert async ToolResult variants (`social_sentiment`, `market_brief`, `scam_radar`) to sync for ergonomics
  - Add `rugbrain_ingest` tool for programmatic document ingestion
  - Add `rugbrain_collections` tool to list owned collections + dimensions
  - Add `rugbrain_lane_report` tool for diagnostics
  - Implement tool-specific rate limiting (lower limits for expensive ops)

### Ingest Pipeline
- **Done**: 50 registered sources with deterministic doc_id, base/source/lang chain metadata, write-gate protection
- **Remaining**:
  - Fix Redis content key lane segregation (separate base/resolved namespaces)
  - Add source health checks (circuit breaker for flaky upstreams)
  - Implement dead letter queue for failed embeddings
  - Add source-specific rate limits and concurrency controls
  - Add schema validation for incoming IngestDoc metadata

### Collections/Storage
- **Done**: 
  - Lane resolution (base → `_8b` twin) for writes and reads
  - Unified reads via `rag_documents` with payload filtering
  - Interim shadow namespace support for migration
  - Owned collections gate (write protection)
  - Dimension guard prevents width mismatch
- **Remaining**:
  - Migrate remaining pinned collections (`coding_knowledge`, `public_crypto_patterns`, `rug_events`, `solana_patterns`, `wallet_clusters`, `youtube_videos`) to canonical 4096 lane
  - Retire 2560 generation after migration completes (free 65% RAM)
  - Add collection-level TTL for ephemeral data (e.g., market_data)
  - Implement collection warming for frequently queried lanes
  - Add vector compression (PQ/OPQ) for 4096-dim collections

### Code Quality
- **Done**: 
  - Lane resolver centralizes base→`_8b` logic
  - Search-side resolution via `resolve_search_collection`
  - Write/read gates prevent accidental collection creation
  - Deterministic UUID v5 point IDs prevent duplicates
  - Fail-closed reads/searches (unknown collections return empty)
- **Remaining**:
  - Extract `_map_collection` calls into single middleware (currently duplicated in 8+ places)
  - Add collection name validation at API boundary (reject non-owned names early)
  - Replace magic strings with enum for collection names in critical paths
  - Add circuit breaker for Qdrant connection failures
  - Implement request ID tracing across API → MCP → Qdrant

## Concrete Next Actions (8-12 verifiable steps)

1. **Add ingest rate limit tier** - Modify `endpoints.py` to define `RATE_LIMITS["ingest"] = 100` and update `/ingest` route to use `"ingest"` instead of `"search"` tier (verify via `curl -H "X-API-Key: ..." -v http://127.0.0.1:8012/api/v1/ingest` returns 429 after 100 req/h)

2. **Convert MCP async tools to sync** - Change `rugbrain_social_sentiment`, `rugbrain_market_brief`, `rugbrain_scam_radar` from `async def` returning `ToolResult` to `def` returning `dict` (verify via `python -m rugbrain_mcp.server` stdio call returns immediate JSON)

3. **Add source validation enum** - In `base.py`, change `source: str = ""` to `source: Literal[<50 source names>] = ""` and add Pydantic validator; verify via POST to `/ingest` with invalid source returns 422

4. **Migrate one pinned collection** - Update `DEFAULT_COLLECTION` in `src/rugbrain/ingest_sources/rug_events.py` from `"rug_events"` to `"rug_events_8b"` and add to `lane_table.py` `_declared_twins()`; verify via Qdrant that new writes go to `_8b` twin

5. **Add connection pooling** - In `store_write.py`, initialize `_client = httpx.AsyncClient(...)` in `__init__` and close in `close()`; verify via `lsof -i` showing persistent connections during load test

6. **Implement ingest health check** - Add `/api/v1/ingest/health` endpoint that checks source registry health and returns per-source status; verify via curl returns 200 with JSON

7. **Add collection TTL support** - Modify `store_write.insert()` to accept optional `ttl_seconds` and pass to Qdrant; verify via Qdrant API that points expire

8. **Add request ID tracing** - Modify `_call()` in MCP server and API endpoints to generate/request `X-Request-ID` and propagate to logs/Qdrant; verify via logs show same ID across hop

9. **Fix Redis lane segregation** - Modify `redis_content_collection()` to accept `resolved: bool = False` parameter and return base name when False, resolved name when True; update write/read paths accordingly; verify via Redis `KEYS rug*_8b:*` shows resolved-name keys

10. **Add OpenAPI completion** - Add `tags` parameter to all 60+ route operations; verify via `/openapi.json` shows all operations tagged

11. **Implement dead letter queue** - Add `store_write.insert()` failure path that sends failed documents to `/ingest/dlq` endpoint; verify via POST with bad embedding returns 200 with dlq_status: queued

12. **Add Prometheus metrics** - Install `prometheus-fastapi-instrumentator` and add `/metrics` endpoint; verify via `curl http://127.0.0.1:8012/metrics` shows http_requests_total, qdrant_latency_seconds
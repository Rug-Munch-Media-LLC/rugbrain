# ARCHITECTURE-2026 — RugBrain + RMI Codebase MCP (RAG + MCP)

> 2026 enterprise architecture for the RugMunch crypto-intelligence
> retrieval stack: what ACTUALLY runs as of 2026-08-16. Supersedes
> `ARCHITECTURE.md` (stale: 20 collections, no hybrid, no MCP split).
> Companion docs: `DATA-PLACEMENT.md` (canonical split), `COLLECTION_REGISTRY.md`
> (live inventory), `graphrag-architecture.md` (design contract, not yet shipped).

Status: **canonical** · owner=Rug Munch Media LLC Engineering · last_updated=2026-08-16

---

## 1. Two systems, one store

One shared Qdrant instance (`localhost:6333` / `rmi-qdrant:6333`) serves two
purpose-built layers that must never mix data. The decision rule
(`DATA-PLACEMENT.md`) is: **Code → MCP. Knowledge → RAG.**

### 1.1 `rmi-codebase-mcp` — "Context7 for crypto" (code reference)

The fleet's own code-reference tool: labeled, searchable crypto source so
agents can *look at code* before trusting a pattern.

| Collection | Contents | Verdict vocabulary | Tools |
|---|---|---|---|
| `codebase_reference` | Reference snippets from audited protocols/libraries (OZ, Uniswap v2/v3-core, Aave v3-core, Compound, Solady) **+ DeFiHackLabs exploit PoCs** | `clean` / `vulnerable` / `malicious` / `defense` (exactly 1) | `resolve_library`, `query_codebase` |
| `security_patterns` | Vulnerability + defense patterns — SWC Registry, Consensys, DeFiHackLabs PoCs, curated 189-pattern core | `malicious` / `defense` / `mixed` / `weakness` / `unknown` | `query_security_context`, `inject_security_pattern`, `list_patterns`, `delete_pattern` |

Plus 8 codebase-intelligence graph tools (`rmi_search`, `rmi_trace`,
`rmi_impact`, `rmi_find_similar`, `rmi_tags`, `rmi_architecture`,
`rmi_status`, `rmi_refresh`) over the codebase-memory graph.

**Enums (schema in `CONTEXT7-REFERENCE.md`):** 6 languages (solidity/rust/
python/typescript/haskell/ruby), 7 ecosystems, 13 categories, 9 snippet_types,
14 vulnerability categories (Reentrancy → ZK-Underconstrained). `verdict` is
first-class metadata: a consuming agent knows *before reading* whether a
snippet is safe to copy.

### 1.2 `rmi-rag` (RugBrain) — knowledge / incidents / wallets

Natural-language facts layer: incident post-mortems, blacklists, audits,
regulation, news, wallet/entity graphs. **40+ Qdrant collections** —
21 active/fresh (e.g. `threat_intel` 11.9 K, `defi_exploits` 13.4 K,
`news_v2` 4.1 K, `wallet_v2` 13.5 K, `regulation_enforcement`, `contract_audits`)
plus ~20 small/tooling collections (`rgm_*` wallet graph 10 K, `scanner_v2`…).
Full freshness/status in `COLLECTION_REGISTRY.md` (captured 2026-08-08).

**Historical-only flags (do not treat as current):** `scam_patterns` +
`known_scams` are CryptoScamDB-frozen 2022-06-28; `hacks`/`hacks_v2` source
(Rekt RSS) is dead (HTTP 500, client-rendered). Replacement universe:
DeFiLlama hacks API → `threat_intel` (612 incidents, idempotent).

**GraphRAG (`graphrag-architecture.md`) is a DESIGN CONTRACT, not shipped.**
Scam-wallet graph lanes over the shared rmi-backend Neo4j are specified
(Phase 0–2), with non-negotiables: one graph store, one vector store,
fail-open graph lane / fail-closed vector lane, 5 s bot RAG budget. As of this
doc: the vector hybrid is what runs.

---

## 2. Retrieval design

### 2.1 Hybrid vector + sparse (RAG collections)

RugBrain's `search/engine.py` runs a **three-pillar hybrid**: dense
(BGE-M3 1024-dim via Infinity), sparse (in-memory Okapi BM25 over
`rugbrain.splade_bm25.BM25Index`), and entity lanes, fused with
**reciprocal rank fusion (RRF)** then **MMR dedup**. Optional
post-fusion: rerank (self-hosted Qwen3-Reranker-8B, ≤ 25 candidates), HyDE /
multi-query expansion, Redis query cache. Per-collection payload filters
(chain, category, severity, verdict, address) narrow before scoring.

**Latency posture:** vector lane fans out over the bot's 5 collections
(`security_patterns`, `codebase_reference`, `defi_exploits`,
`contract_security`, `scam_patterns`, + `scanner_v2` for token questions)
via `asyncio.gather`. `RAG_SEARCH_TIMEOUT = 5.0` s in
`app/routers/chat/_rag.py` gates the chat path; `RAG_CONTEXT_MAX_CHARS =
1800` caps prompt context. **Note:** the p50 45 ms / p99 180 ms numbers in
the old `ARCHITECTURE.md` are unverified in 2026 — re-measure (see §5).

### 2.2 Qdrant indexing

Qdrant HNSW with payload indexes on filterable fields (`library_id`,
`verdict`, `category`, `chain`, `severity`, `source`); deterministic point
ids make upserts idempotent; payload filtering runs pre-vector (scroll
filters by `library_id` in ingest, `Filter`+`FieldCondition` at query
time). `codebase_reference` is created dense-only (1024-d, cosine) by
`ingest_codebase_reference.py`; RAG collections created by ingest scripts
carry the sparse/BM25 config where hybrid is enabled.

### 2.3 The bridge — `mcp_bridge.py` (the seam)

`src/rugbrain/mcp_bridge.py` is the ONLY place RAG reaches MCP. Direction is
one-way: **RAG → MCP; MCP never calls RAG.**

| Function | MCP tool |
|---|---|
| `search_codebase(q)` | `rmi_search` |
| `trace_codebase(fn)` | `rmi_trace` |
| `get_security_context(q)` | `query_security_context` |
| `resolve_library(q)` | `resolve_library` |
| `query_codebase(lib, q)` | `query_codebase` |
| `augment_code_query(q)` | search + security context in one call |
| `sync_security_patterns(n)` | pull MCP patterns into RAG |

**Execution strategy, in order:** (1) in-process direct import when
`fastmcp`+`chromadb` importable; (2) subprocess runner
(`scripts/mcp_tool_runner.py`, `RMI_MCP_PYTHON` default x402 venv,
90 s timeout); (3) RAG fallback onto RugBrain's own coding/security
knowledge — the bridge degrades gracefully when MCP is unreachable.
No secrets cross the boundary: only tool names + kwargs, runner never logs
bodies.

---

## 3. Ingestion

### 3.1 Catalog-driven (rmi-codebase-mcp)

Corpus is defined by JSON catalogs + a small hardcoded curated set:

- `codebase_catalog.json` (63 entries, clean) + `vulnerable_catalog.json`
  (47 entries, malicious/vulnerable/defense) — merged with the hardcoded
  `CLEAN_CATALOG` (6 audited libs with `path_filters`) + `POC_ENTRY`
  (DeFiHackLabs).
- Only code-bearing languages are wired (`solidity`, `vyper`); prose-only
  catalog entries are skipped → they belong to RugBrain (DATA-PLACEMENT).
- `repos.json` remains the legacy 31-repo catalog for the pattern corpus.

### 3.2 AST structural chunking

`scripts/ingest_codebase_reference.py` chunks via the shared
`chunker.chunk_solidity` (AST-aware, `CHUNKER_BACKEND`): whole-unit snippets
for small contracts/interfaces/libraries (≤ 400 lines); large contracts
split into **member-level snippets** (function, modifier→`guard`,
constructor, receive/fallback, error, event, struct/enum, using-for), each
carrying `function_name` and a NatSpec-derived description. `.rs`/`.vy`/
`.move` fall back to whole-file module snippets (20 K char cap). Excludes
`node_modules`, tests, mocks, lib, audit/analysis dirs. PoC catalog parsed
from DeFiHackLabs incident READMEs (`### YYYYMMDD …` headers + `forge test
--contracts` lines), prioritized by attack-class keywords, deduped by
`poc_rel`, capped (`--max-pocs 120`).

### 3.3 Proxy rotation + idempotency

- **WebShare residential proxy** for `git clone --depth 1`: `_get_proxy()`
  honors `WEBSHARE_PROXY` env, else shells a fleet proxy helper (env
  `WEBSHARE_PROXY`) that reads secrets from the secrets manager. Rotating
  IPs dodge GitHub per-IP clone rate limits on catalog sweeps. Repos cached
  under `/path/to/repos/*` / `.repos/`; clones land in
  `/tmp/opencode/ingest-*`.
- **Idempotent IDs:** UUIDv5 in DNS namespace from
  `codebase-reference:{library_id}:{file_path}:{title}` — re-runs overwrite
  in place; pre-embed scroll of existing `(file_path, title)` per library
  skips already-ingested snippets. **Re-running a catalog entry is a no-op.**
- Embed batch 16, upsert batch 64 (`wait=True`). Post-ingest `verify()` runs
  4 probe queries + verdict/library distribution counts.
- **Refresh:** `scripts/auto_update.sh` on a 6 h timer (repos, graph,
  patterns). RAG: cron — news 15 min, knowledge hourly, threat_intel +
  regulation every 6 h, Qdrant backup daily 03:00 (`fleet-infra`).

---

## 4. Production posture

- **Qdrant is the SOLE vector store.** ChromaDB dropped after the migration
  (`seed_qdrant.py`, `chroma_db/README.md`); all reads/writes are Qdrant.
  *Legacy naming drift:* the bridge docstring still says "pull MCP ChromaDB
  patterns" — the data path is Qdrant; rename is cosmetic debt.
- **Graceful degradation, layered:**
  - MCP down → bridge falls back to RAG's own coding knowledge.
  - Infinity/embedding down → search 503 per contract (fail-closed vector).
  - Neo4j (future GraphRAG) down → zero graph evidence, vector lane intact
    (fail-open graph).
  - Guardrail failure → passthrough, never raise.
- **Guardrails** (`rugbrain.guardrails`, additive, never raise):
  `sanitize_query` neutralizes prompt-injection queries before they reach MCP
  or fallback (response carries `guard {flagged, reasons, sanitized,
  refused}`); `redact_output` strips private keys, mnemonics, API keys,
  internal hostnames, private IPs from results.
- **Observability + eval — honest gaps:**
  - ✅ Post-ingest probe queries + verdict distributions (codebase ingest).
  - ✅ `COLLECTION_REGISTRY.md` freshness capture (2026-08-08) used by the
    freshness agent to gate time-sensitive signals.
  - ⚠️ **No continuous eval harness.** No RAGAS/recall baseline exists
    today; the GraphRAG contract names RAGAS recall as a *future* gate.
  - ⚠️ **No automated retrieval-quality regression gate** on catalog
    additions; `verify()` is manual smoke only.
  - ⚠️ **No Qdrant metrics pipeline** (collection sizes, QPS, latency)
    documented; old latency figures are stale (see §2.1).

---

## 5. Next 90 days — ranked by impact

| # | Work | Why | Effort |
|---|---|---|---|
| 1 | **Stand up a retrieval eval harness** (RAGAS-style golden set over the bot's 5 collections + `codebase_reference` probes) and baseline it | Nothing else can be optimized without a recall/grounding score; blocks #4–6 confidence | M |
| 2 | **Ship GraphRAG Phase 0** (Neo4j constraints + `ingest_graph.py` from `graphrag-architecture.md` §6; no retrieval change) | Unlocks scam-network evidence; zero blast radius, contract already written | M |
| 3 | **GraphRAG Phase 1** (graph lane in `/api/v1/rag/v2/search`, Redis subgraph cache, RRF fusion §2.4, p95 ≤ 3 s) | Biggest single retrieval upgrade available; fail-open keeps risk low | L |
| 4 | **Refresh `ARCHITECTURE.md` latency/throughput numbers** and add Qdrant metric collection (collection sizes, p50/p99, QPS) | Old p50 45 ms / p99 180 ms figures are unverified; ops needs real baselines to detect regressions | S |
| 5 | **Complete `codebase_reference` corpus** — run JSON catalogs to full coverage (Rust/Solana programs, vyper), re-verify verdict distribution | Corpus breadth is the retrieval ceiling for "Context7 for crypto" | M |
| 6 | **Replace frozen blocklist sources** (`known_scams`/`scam_patterns`) with live feeds (ScamSniffer, Forta, MetaMask eth-phishing-detect per GraphRAG §3) | 4-year-stale data pollutes current-risk answers today | M |
| 7 | **Bridge rename/cleanup** (`sync_security_patterns` ChromaDB→Qdrant naming; retire legacy `seed_qdrant.py` references) | Cosmetic but removes operator confusion at the seam | S |

Cross-cutting: keep the MCP fleet at cap (no new servers — the bridge is the
integration), and keep `DATA-PLACEMENT.md` as the gate for every new source.

---

*End. Conflicts: this doc reflects live behavior as of 2026-08-16; where
`ARCHITECTURE.md`/`graphrag-architecture.md` disagree, the newer, verified
state wins. GraphRAG sections describe the design contract only.*

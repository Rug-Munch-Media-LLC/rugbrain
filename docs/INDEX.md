# RugBrain — Documentation Index

**The entry point.** Read top to bottom the first time; afterwards jump by question.
If a question is not in this table, the answer is probably in the code or in
`docs/COLLECTION_REGISTRY.md`, not in prose.

Status legend: **CURRENT** (matches code) · **CURRENT-WITH-GAP** (right, but predates a
later change, noted) · **SUPERSEDED** (kept for history, banner on the file) · **PENDING**
(owned by another lane, not on disk yet — do not cite as if it exists).

---

## 1. Reading order

| # | Document | Status | The question it answers |
|---|---|---|---|
| 1 | [`README.md`](../README.md) | CURRENT-WITH-GAP | What is RugBrain, how do I run it? (Its "Postgres" prerequisite is unbacked — see §3.) |
| 2 | [`docs/USING-RAGBRAIN.md`](USING-RAGBRAIN.md) | **CURRENT** | I am an agent — how do I query it, and how do I cite what I got back? |
| 3 | [`docs/ARCHITECTURE-2026.md`](ARCHITECTURE-2026.md) | CURRENT-WITH-GAP | What actually runs, and which store holds what? (Predates the audience boundary — see `PROJECT_STATE`.) |
| 4 | [`ARCHITECTURE-V2.md`](../ARCHITECTURE-V2.md) | CURRENT-WITH-GAP | Why is it shaped this way, what was the consolidation plan, and which phases really closed? |
| 5 | [`docs/PROJECT_STATE.md`](PROJECT_STATE.md) | **CURRENT** | What changed most recently, what is blocked, what is next? |
| 6 | [`docs/COLLECTION_REGISTRY.md`](COLLECTION_REGISTRY.md) | CURRENT | Which collections exist and what is in them? |
| 7 | [`docs/INGEST-COVERAGE.md`](INGEST-COVERAGE.md) | **PENDING** | Which sources are actually ingested, at what cadence, with what gaps? |
| 8 | [`docs/TA-CAPABILITY.md`](TA-CAPABILITY.md) | **PENDING** | What technical analysis can RugBrain do, and what it cannot? |
| 9 | [`docs/CONTENT-CHAIN.md`](CONTENT-CHAIN.md) | **PENDING** | How does ingested evidence become published content? |
| 10 | [`docs/MUTATION_BASELINE`](../MUTATION_BASELINE.md) / [`docs/CI-GATES.md`](CI-GATES.md) | CURRENT | What gates block a merge, and what is the debt baseline? |

---

## 2. By question

### Ingest coverage — "do we actually have this data?"
- [`docs/INGEST-COVERAGE.md`](INGEST-COVERAGE.md) — **PENDING** (lane 1).
- Until it lands: [`docs/COVERAGE-AUDIT-2026-09-28.md`](COVERAGE-AUDIT-2026-09-28.md),
  [`docs/COVERAGE-GAP-ANALYSIS-2026-09-27.md`](COVERAGE-GAP-ANALYSIS-2026-09-27.md),
  [`docs/COVERAGE-OSS-STACK.md`](COVERAGE-OSS-STACK.md) are the dated snapshots; they are
  point-in-time, never assume they are today's number.
- Source inventory: [`docs/COLLECTION_REGISTRY.md`](COLLECTION_REGISTRY.md),
  [`docs/SOURCES-DEVDOCS.md`](SOURCES-DEVDOCS.md), [`docs/SECURITY-FEEDS.md`](SECURITY-FEEDS.md),
  [`docs/ADDING-A-SOURCE.md`](ADDING-A-SOURCE.md) (how to wire a new one).
- Liveness check: `python3 scripts/check-collection-staleness.py`.

### Retrieval contract — "what does a search actually promise me?"
- [`docs/RETRIEVAL-MAP.md`](RETRIEVAL-MAP.md) — routes, arms, and where hybrid/rerank apply.
- [`docs/DUAL_TIER_RAG.md`](DUAL_TIER_RAG.md) — the 2560-dim canonical lane vs the `_8b` 4096-dim lane.
- [`docs/EMBEDDING-RERANKING-STACK.md`](EMBEDDING-RERANKING-STACK.md) — models, dimensions, fallbacks.
- [`docs/DIM-DRIFT-GATE.md`](DIM-DRIFT-GATE.md) — the gate that stops mixed-dimension writes.
- [`docs/RAG-MCP-BOUNDARY.md`](RAG-MCP-BOUNDARY.md) — knowledge → RugBrain, code → ContextCrypto.
- [`docs/DATA-PLACEMENT.md`](DATA-PLACEMENT.md) — the one-line placement rule.

### Technical analysis (TA) — "what does the engine see in the candles?"
- [`docs/TA-CAPABILITY.md`](TA-CAPABILITY.md) — **PENDING** (lane 2).
- The shipped surface today is HTTP only: `GET /api/v1/ta/signals`, `GET /api/v1/ta/stats`
  (`src/rugbrain/routes/ta.py`). **No `rugbrain-mcp` tool exposes TA** — an agent must call
  the HTTP route. See [`USING-RAGBRAIN.md`](USING-RAGBRAIN.md) §3.

### Content chain — "how does a retrieved fact become a post?"
- [`docs/CONTENT-CHAIN.md`](CONTENT-CHAIN.md) — **PENDING** (lane 3).
- Interim: [`docs/CONTENT-PIPELINE.md`](CONTENT-PIPELINE.md),
  [`docs/CONTENT-STYLE.md`](CONTENT-STYLE.md) (editorial rules),
  `src/rugbrain/routes/content.py` (`/drafts`, `/ack`, `/trending`), `src/rugbrain/routes/daily_rug.py`.

### Prediction corpus / market knowledge — "what do we hold about markets and odds?"
- **PENDING** — no lane doc yet. Do not guess a collection name.
- What exists: [`docs/MARKET-DATASETS-2026-10-23.md`](MARKET-DATASETS-2026-10-23.md) (dated
  dataset survey), [`docs/DECISION-EMPTY-MARKET-COLLECTIONS.md`](DECISION-EMPTY-MARKET-COLLECTIONS.md)
  (why some market collections are empty — read before reporting a bug),
  [`docs/DATASET-BACKLOG.md`](DATASET-BACKLOG.md), [`docs/TIER-MATRIX.md`](TIER-MATRIX.md).
- Retrieval entry point that is verified: `rugbrain_market_brief()` fuses `news_v2`,
  `social_intel`, `education`. Prediction-market/odds data is **not** among them.

### Debt and gates — "what will stop my PR, and what may I not grow?"
- [`../MUTATION_BASELINE.md`](../MUTATION_BASELINE.md), [`docs/EVALS.md`](EVALS.md),
  [`docs/eval-baseline-2026-08.md`](eval-baseline-2026-08.md),
  [`docs/CI-GATES.md`](CI-GATES.md), [`docs/DIM-DRIFT-GATE.md`](DIM-DRIFT-GATE.md),
  [`docs/PR_CHECKLIST.md`](PR_CHECKLIST.md).
- Runners: `scripts/check-structure.py`, `scripts/check-monkeypatch.py`,
  `scripts/check-antipatterns.py`, `scripts/check_dim_drift.py`.

### Operations / permanence
- [`docs/INTEGRATION-RUNBOOK.md`](INTEGRATION-RUNBOOK.md), [`docs/PERMANENCE-CONTRACT.md`](PERMANENCE-CONTRACT.md)
  (B2 parquet snapshots), [`docs/MODEL_SERVICES_PLATFORM.md`](MODEL_SERVICES_PLATFORM.md),
  [`docs/MCP-SECURITY.md`](MCP-SECURITY.md).

---

## 3. Known-stale documents (do not cite as fact)

| Document | Verdict | Why |
|---|---|---|
| [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) | **SUPERSEDED** | OpenAI embed fallback, pre-`_v2` collection names, unsourced p50/p99/QPS figures. Banner on file. |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | **SUPERSEDED** | 2026-07-31 snapshot of the "two RAGs" status quo. Banner on file. |
| `README.md` prerequisites "Qdrant, Redis, **Postgres**" | **STALE CLAIM** | `pyproject.toml` declares no Postgres client (`psycopg`/`asyncpg`/SQLAlchemy). Treat Postgres as not required until someone shows the import. |
| [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md) | CURRENT-WITH-GAP | Says the tool surface lives in `src/rugbrain/rugbrain_mcp/server.py`; tools were split into `tools_search.py`, `tools_intel.py`, `tools_market.py`, `tools_zeroday.py`. Content still usable; that pointer is wrong. |
| [`docs/ARCHITECTURE-2026.md`](ARCHITECTURE-2026.md) | CURRENT-WITH-GAP | Dated 2026-08-16 — older than the audience-boundary work in `PROJECT_STATE.md` (2026-09-24). |
| [`ARCHITECTURE-V2.md`](../ARCHITECTURE-V2.md) §0.1 phase list | CURRENT-WITH-GAP | Its `[NOT DONE]`/`[PARTIAL]` marks were true on 2026-08-01 only; `scripts/consolidate-active.sh` and `scripts/consolidate_to_rag_documents.py` postdate them. |
| `README.STALE-CLONE.md` | BY DESIGN | Self-labelled trap for stale clones; not documentation. |

Rules for keeping this honest:
1. A claim is true if a file in `src/`, `scripts/` or `config/` implements it. Otherwise it is unverified.
2. Dated audit snapshots (`*-2026-*`) are evidence at their date, never a live metric.
3. When you supersede a doc, add a banner and a pointer here — never delete it.

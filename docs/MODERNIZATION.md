# RugBrain Modernization & De-God Blueprint

**Status:** draft / plan-of-record · **Date:** 2026-09-18 · **Scope:** `rugbrain` (standalone RAG)
**Baseline commit (Talos `d36bf15`):** `fix(rag): point the chat lane at the real model alias (qwythos-9b)`
**Method:** all line counts, function counts and file inventories in this document were produced by
`wc -l` over `src/rugbrain/**/*.py` and `rg -c '^(async )?def |^class '` on the working tree at commit
`d36bf15` (local mirror `/tmp/opencode/rugbrain`, remote `/srv/work/repos/rugbrain`). No estimates.

---

## 0. Executive summary

RugBrain is a ~64.5k-line, 202-module Python RAG engine that still carries the architectural shape of
its origin as a subpackage of `rmi-backend`. Three problems define the modernization:

1. **God files.** 36 modules exceed 500 lines; 116 exceed 200. The worst (`search/engine.py`, 1771 lines,
   one 650-line function) concentrate routing, orchestration, formatting and fusion in a single file.
2. **Latent backend coupling.** `import_guard.py` injects 24 `app.*` stub modules into `sys.modules`;
   `firehose/__init__.py` posts ingestion to `http://localhost:8000/api/v1/rag` (the backend), and
   `firehose/twitter.py` has a DataBus call with **no** standalone fallback. The repo *can* fall back for
   news/prediction/social but not for CT rundown or for the primary ingest path.
3. **Missing test/eval enforcement.** 102 test files and a real eval harness exist, but no retrieval-quality
   regression gate runs in CI, and the Makefile still says `test: placeholder - no tests yet`.

The plan below fixes these in eight phases with shrink-only ratchets so the codebase can only improve.

---

## 1. Module inventory

### 1.1 Repository shape

| Metric | Value |
|---|---|
| `.py` files under `src/rugbrain/` | 202 |
| Total lines under `src/rugbrain/` | 64,514 |
| Files > 200 lines | 116 |
| Files > 500 lines (HARD-FAIL tier) | 36 |
| Test files under `tests/` | 102 |
| `tests/integration/` | 1 (`test_dual_tier_live.py`) |
| Subpackages | `eval/`, `firehose/`, `ingest_sources/`, `monitors/`, `routes/`, `search/` |
| CI workflow of record | `.forgejo/workflows/ci.yml` |
| Stale/duplicate workflow | `.github/workflows/ci.yml` (ubuntu-latest + uv, diverged) |

### 1.2 Top offenders (>500 lines) — full list with function/class counts

`fn` = count of `^(async )?def ` + `^class ` at module top level.

| # | File | Lines | fn | Primary responsibility |
|---|---|---|---|---|
| 1 | `search/engine.py` | 1771 | 19 | Search orchestration: scam/coding query detect, collection resolution, hybrid+vector fusion, 3-pillar, parent fetch |
| 2 | `realtime_alerts.py` | 1655 | 42 | Real-time alert fan-out (webhook/NATS/stream consumers) |
| 3 | `news_rating.py` | 1078 | 23 | News source credibility + article rating |
| 4 | `alerts.py` | 1078 | 25 | Alert ingestion/definitions (ClickHouse/NATS) |
| 5 | `mcp_bridge.py` | 1076 | 26 | MCP tool surface over RAG |
| 6 | `app_chunking.py` | 1046 | 31 | Parent/child app-aware chunking |
| 7 | `guardrails.py` | 935 | 10 | Output guardrails / hallucination + jailbreak checks |
| 8 | `monitors/social_sentiment.py` | 906 | 17 | Social sentiment monitor |
| 9 | `liquidation_heatmap.py` | 878 | 18 | Liquidation heatmap analytics |
| 10 | `qdrant_store.py` | 874 | 12 | Qdrant client, filters, BM25 sparse, `QdrantVectorStore` (480-line class) |
| 11 | `crypto_embeddings.py` | 818 | 3 | Crypto embedding lanes |
| 12 | `main.py` | 811 | 4 | FastAPI app + 10 background pollers in lifespan |
| 13 | `firehose/__init__.py` | 777 | 6 | Firehose engine + rate/quality + backend `RAG_API` ingest |
| 14 | `fear_index.py` | 773 | 31 | Fear & Greed index polling/derivation |
| 15 | `prompt_compression.py` | 734 | 23 | Prompt compression |
| 16 | `bundle_cluster.py` | 720 | 9 | Transaction bundle clustering |
| 17 | `splade_bm25.py` | 719 | 8 | SPLADE/BM25 sparse retrieval |
| 18 | `youtube_ingester.py` | 671 | — | YouTube ingestion + scan loop |
| 19 | `sentiment_engine.py` | 669 | 11 | Sentiment analysis + router |
| 20 | `contract_analysis.py` | 657 | — | Smart-contract analysis |
| 21 | `routes/search.py` | 646 | 5 | Search HTTP routes |
| 22 | `web_crawler.py` | 637 | 11 | Crawler (crawl4ai optional) |
| 23 | `eval/retrieval_eval.py` | 632 | 17 | Live-endpoint retrieval eval CLI |
| 24 | `ingest_sources/incidents.py` | 615 | 19 | Incident ingest source |
| 25 | `firehose/market_sources.py` | 603 | 14 | Market data sources |
| 26 | `multi_embedder.py` | 589 | — | Multi-backend embedding fan-out |
| 27 | `monitors/twitter_tracker.py` | 564 | 14 | Twitter/X tracking |
| 28 | `historical.py` | 564 | 15 | Historical backfill; posts to backend `/api/v1/rag/ingest` |
| 29 | `news_clustering.py` | 526 | 19 | News clustering |
| 30 | `search/filters.py` | 523 | — | Search filters |
| 31 | `news_ranking.py` | 521 | 20 | News ranking |
| 32 | `permanence.py` | 519 | 15 | B2 backup/retention (name = the B2 backup job) |
| 33 | `monitors/token_distribution.py` | 516 | 20 | Token distribution monitor |
| 34 | `crypto_schema.py` | 514 | — | Crypto document schema |
| 35 | `embeddings.py` | 510 | 15 | Embedding lanes/backends |
| 36 | `ingest_sources/telegram_userbot.py` | 504 | 16 | Telegram userbot ingest |

**Flagged god files (confirmed, not assumed):**
- `search/engine.py` — 1771 lines, 19 top-level defs, single `three_pillar_search()` spans lines 944–1597
  (~650 lines). This is the highest-risk file in the repo.
- `qdrant_store.py` — 874 lines; `QdrantVectorStore` class spans 390–869 (~480 lines).
- `main.py` — 811 lines; `lifespan()` starts 10+ pollers inline (fear, realtime alerts, news, mempool,
  MEV, governance, podcast, smart-money, YouTube, newsletter) with per-poller try/except at lines 210–395.
- `realtime_alerts.py` (1655) and `alerts.py` (1078) are near-duplicates by name and responsibility and
  should be evaluated for consolidation.
- `prompt_compression.py`, `fear_index.py`, `app_chunking.py` each have 23–31 top-level defs with no class
  boundary — these are "function bag" god files.

### 1.3 Function-dense modules (even if <500 lines)

| File | Lines | fn |
|---|---|---|
| `compat.py` | 293 | 21 |
| `news_ranking.py` | 521 | 20 |
| `monitors/token_distribution.py` | 516 | 20 |
| `ingest_sources/incidents.py` | 615 | 19 |
| `monitors/hyperliquid_monitor.py` | 476 | 18 |
| `liquidation_heatmap.py` | 878 | 18 |
| `system_health.py` | 391 | 17 |
| `parquet_analytics.py` | 374 | 17 |
| `monitors/social_sentiment.py` | 906 | 17 |
| `monitors/content_digest.py` | 495 | 17 |
| `eval/retrieval_eval.py` | 632 | 17 |

### 1.4 Directive engine state

- `import_guard.py` (243 lines) installs `_APP_MODULES` — **24** `app.*` stub modules — into `sys.modules`
  as a compatibility layer. Every one is a deletion candidate once standalone is proven.
- `compat.py` (293 lines) provides 24 native replacements for former `app.*` dependencies (Redis, entity
  extraction, ANN, semantic cache, reranker, confidence, etc.). Retained by design; should be renamed and
  documented as `support/`.
- `scripts/check-antipatterns.py` + `scripts/antipattern-baseline.json` already implement a per-repo
  ratchet. Baseline (this commit): `monkeypatch=396`, `god-file=57`, `bare-except=325`,
  `long-function=95`, `too-many-params=76`, `fstring-logging=49`, `slop-markers=2`, `eval-exec=1`,
  `wildcard-import=1`, **total=1002**. This is the existing ratchet substrate to extend.

---

## 2. Canonical target structure

### 2.1 Target package layout

```
src/rugbrain/
  __init__.py
  __main__.py
  main.py                  # app factory only (<200 lines)
  api/                     # HTTP layer — one router per file
    __init__.py            # router registry
    search.py  ingest.py  analyze.py  intel.py  monitors.py  content.py
    rag_tools.py  search_federated.py  alerts.py  health.py
  retrieval/               # query → results
    engine.py              # thin orchestrator (compose, don't implement)
    fusion.py              # RRF / hybrid fusion
    filters.py
    rerank.py
    query_transform.py
    pillar_scam.py
    pillar_coding.py
    pillar_market.py
  ingestion/
    firehose.py            # engine + scheduling (no backend RAG_API)
    direct_sources.py      # upstream fetchers (exists)
    quality.py             # score/dedup/rate
    chunking/
      app.py  tree_sitter.py  parent_child.py  solidity.py
  ingest_sources/          # one upstream per file (already)
  eval/
    golden_set.jsonl
    ndcg.py  metrics.py  run_eval.py  retrieval_eval.py
    harness.py             # NEW: CI-callable backtest runner
    gates.py               # NEW: thresholds + regression compare
  stores/
    qdrant_store.py        # transport only
    filters.py  collections.py  bm25.py
  support/
    compat.py              # renamed from compat.py
    redis.py  llm.py  config.py  observability.py
  monitors/                # already one-per-file
```

### 2.2 Module-size policy

| Tier | Rule | Enforcement |
|---|---|---|
| <= 200 lines | Preferred for all new modules | advisory |
| 201–500 lines | Allowed with a written justification in the module docstring (`# SIZE-JUSTIFICATION: ...`) | lint check |
| > 500 lines | **HARD FAIL** for new/modified files | CI size ratchet |
| One primary function per module | New code: one exported entry point; helpers private or split | review |
| No new module > 300 lines in a single PR | mechanical | review |

Existing files above the cap are **grandfathered via a shrink-only allowlist** keyed by
`path:lines`. A PR may not increase any listed value, and may not add a path. Removing a path once it
drops under 500 is encouraged. (See §4 gate `size-ratchet`.)

### 2.3 Zero monkeypatching

- New code: no `monkeypatch.setattr`, no `sys.modules[...] = ...`, no runtime `setattr` on instances to
  fabricate attributes.
- Tests: monkeypatching is permitted only at network boundaries via `respx` (already a dev dependency).
  Current test monkeypatch count is **396** (baseline). Ratchet is **decrease-only**.
- Source-side monkeypatch coupling exists in `endpoints.py`, `routes/search.py`, `routes/analyze.py`
  (documented "tests monkeypatch this module object at call time"). Removing it requires moving the
  patch points behind injectable dependencies first (see Phase 3/5).

### 2.4 Import boundary

- No module under `src/rugbrain/` may import `app.*` (already a CI grep), and additionally no module may
  import identifiers containing `backend`, `rmi_backend`, `rmi-backend`, or `databus` as an import path,
  with a single allowlist entry for `support/compat.py` and `system_health.py` (health probe only).
- `import_guard.py` is **deleted** once §5 cutaway is complete; until then it is the only file allowed to
  construct `app.*` module names.

### 2.5 Consolidation map (current → target, by phase)

| Phase | Current | Target |
|---|---|---|
| P3 | `search/engine.py` | `retrieval/{engine,fusion,pillar_*}.py` |
| P3 | `search/filters.py`, `search/rerank.py` | `retrieval/filters.py`, `retrieval/rerank.py` |
| P3 | `qdrant_store.py` | `stores/{qdrant_store,filters,collections,bm25}.py` |
| P4 | `firehose/__init__.py`, `firehose/news.py`, `firehose/twitter.py` | `ingestion/{firehose,quality}.py` + upstream fetchers |
| P4 | `historical.py` | `ingestion/historical.py` (direct store, no backend POST) |
| P5 | `realtime_alerts.py` + `alerts.py` | `alerts/{engine,sources}.py` (dedupe shared logic) |
| P6 | `app_chunking.py`, `tree_sitter_chunking.py`, `solidity_chunker.py` | `ingestion/chunking/*` |
| P6 | `news_rating.py`, `news_ranking.py`, `news_clustering.py` | `ingestion/news/{rating,ranking,clustering}.py` |
| P7 | `monitors/*` (10 files) | stay; each already one monitor, split only if >500 after phase |
| P8 | `compat.py`, `import_guard.py` | `support/compat.py`; `import_guard.py` deleted |

---

## 3. Testing & backtesting

### 3.1 Current test inventory

- **102** test modules under `tests/` (top-level + `tests/integration/`).
- Coverage gate: `pytest --cov=rugbrain --cov-fail-under=60` and `diff-cover --fail-under=60` in
  `.forgejo/workflows/ci.yml` (`quality` job). Config: `[tool.coverage.report] fail_under = 60`.
- Test quality tooling: `mutmut` configured (`[tool.mutmut] source_paths=["src/rugbrain"]`) but the
  `mutation-test` CI job is `continue-on-error: true` (informational).
- `respx>=0.21.0` available for HTTP mocking.
- **Gap:** `Makefile` `test:` target is still `## Run tests (placeholder - no tests yet)` and runs nothing.
  Local `make test` silently passes. Must be fixed to mirror CI.

### 3.2 Existing eval assets (reuse, do not rebuild)

- `eval/ndcg.py` — `ndcg_at_k`, `ndcg_from_gains`, `mrr_from_gains`, `recall_at_k`, `hit_rate_at_k`,
  `precision_at_k`, `mrr`.
- `eval/metrics.py` — `faithfulness` (lexical proxy for ragas NLI), `answer_relevance`,
  `context_precision`, `context_recall`, `ragas_available`, `ragas_metric_names`.
- `eval/run_eval.py` — CLI: `python -m rugbrain.eval.run_eval --dataset <jsonl> --top-k 5`; loads a
  dotted `search_fn`, aggregates NDCG@k / recall@k / hit_rate@k / MRR.
- `eval/retrieval_eval.py` — live-endpoint runner: hit@k / recall@k / precision@k + p50/p95 latency
  against `/api/v1/search` (key via `RMI_RAG_API_KEY`).
- `eval/golden_set.jsonl` — **44** labeled crypto queries. `eval/sample_dataset.jsonl` — 10.
- `evaluation.py` (472 lines) — legacy endpoint scorer targeting `http://localhost:8012/api/v1/search`.
- `docs/eval-baseline-2026-08.md` — last recorded baseline.
- Tests already covering eval math: `test_eval_ndcg.py`, `test_eval_metrics.py`, `test_eval_retrieval.py`,
  `test_eval_cli.py`.

### 3.3 Missing test layers

| Layer | Status | Action |
|---|---|---|
| Unit (pure math, filters) | present | keep |
| Route/contract (FastAPI TestClient) | partial (`test_endpoints.py`, `test_routes_search.py`) | add contract snapshots for every router under `api/` |
| Retrieval regression | **missing gate** | P2 harness + CI gate |
| Ingest idempotency / dedup | partial (`test_near_dup.py`) | add per-source contract tests |
| Standalone/no-backend integration | **missing** | `tests/integration/test_standalone_firehose.py` with backend URLs pointed at a closed port |
| E2E (live Qdrant + live embed lane) | 1 integration test | extend, keep out of PR gate |

### 3.4 New RAG backtesting/eval harness design

**Goal:** every PR must prove retrieval quality did not regress against a frozen golden corpus.

**A. Golden crypto corpus (`eval/golden_set.jsonl`)** — extend from 44 to >= 150 labeled queries across
collections `news_v2`, `coding_knowledge`, `defi_exploits`, `scam_patterns`, `incidents`. Each row:
```json
{"query": "...", "collection": "...", "gold_chunk_ids": ["<qdrant-point-id>", ...],
 "gold_chunks": ["<titles>"], "expected_answer": "...", "must_cite": ["..."], "category": "coding|scam|incident|market"}
```
`gold_chunk_ids` are stable Qdrant point IDs (not titles) so recall@k is exact. Backfill them with a
one-shot script; freeze the file hash in the eval baseline.

**B. Harness (`eval/harness.py`)** — new, pure orchestration over existing metrics:
```
run_backtest(corpus: Path, top_k: int, retriever: Callable[[str,str,int], list[Result]],
             baseline: Path | None) -> BacktestReport
```
- Retrieval metrics: `recall@k`, `mrr`, `ndcg_at_k` (reuse `eval/ndcg.py`).
- Groundedness: `faithfulness` + `answer_relevance` (reuse `eval/metrics.py`); optional ragas when
  installed (skip, never fail, when absent).
- Latency: p50/p95 per query (mirror `retrieval_eval.py`).
- Output: JSON report `eval/results/<sha>.json` + markdown summary.

**C. Regression gates (`eval/gates.py`)** — thresholds against committed baseline
`eval/baseline.json` (generated from `docs/eval-baseline-2026-08.md` numbers):
| Metric | Gate |
|---|---|
| NDCG@5 (macro) | not below baseline − 0.02 |
| recall@5 (macro) | not below baseline − 0.03 |
| MRR | not below baseline − 0.02 |
| faithfulness | not below baseline − 0.05 |
| p95 latency | not above baseline × 1.30 |
A regression prints the delta table and exits non-zero. New corpus rows that lower the macro metric must
be justified in the PR body; the baseline file is updated in the same PR (explicit, reviewable).

**D. CI wiring**
- PR job `eval-smoke`: runs harness against a **deterministic in-process retriever** (a fixture returning
  recorded results) — no network, fast, catches harness/metric regressions.
- Nightly job `eval-nightly` (schedule, already exists): runs harness against the **live** Qdrant + embed
  lane, fails on any gate breach, uploads the report. This is the true quality ratchet.
- Published baseline updated only via a dedicated `chore(eval): update baseline` PR.

---

## 4. CI hardening plan

### 4.1 Current CI (`.forgejo/workflows/ci.yml`) — jobs present

`commitlint` (+ `scripts/check-antipatterns.py` ratchet), `quality` (ruff, mypy blocking, pytest
cov>=60, diff-cover>=60), `docker-lint`, `container-scan` (trivy), `security` (gitleaks, bandit -ll),
`decoupled` (grep no `app.*` imports), `supply-chain` (osv-scanner, trufflehog), `nightly-deep-scan`,
`nightly-api-fuzz`, `semgrep` (blocking), `sbom`, `sign-image`, `pip-audit`, `deps-freshness`,
`mutation-test` (informational).

### 4.2 Gaps

1. **Stale duplicate workflow.** `.github/workflows/ci.yml` diverged: `ubuntu-latest`, `uv`, only
   `ruff check src/` (no tests dir), `pytest tests/` with no coverage floor, branch `feat/*`. It should
   be deleted or made a pointer, or it will produce misleading green/red for anyone using GitHub tooling.
2. **Makefile lies.** `test:` target is a no-op placeholder; local dev cannot reproduce CI.
3. **Size not enforced.** `check-antipatterns.py` counts `god-file` (57 baseline) but there is no
   per-file shrink-only line ratchet and no HARD-FAIL at 500 for modified files.
4. **Monkeypatch ratchet not decrease-only enforced in CI** beyond the aggregate antipattern baseline
   (396). Needs a dedicated gate with its own baseline so unrelated changes cannot inflate it.
5. **Coverage floor is global 60% only**; no per-module floor and no ratchet that prevents the percentage
   from drifting down while still >= 60.
6. **Import boundary is a grep, not a test.** It excludes `compat.py` and `_legacy`, does not catch
   `import_guard` module-name strings, and does not guard `backend`/`databus` tokens.
7. **No eval gate in CI** (see §3.4).
8. **Mutation testing informational** — fine, but add a tracked score so it cannot silently rot.
9. **Nightly API fuzz targets backend OpenAPI** (`SCHEMA_URL` default `http://rmi-backend:8000/...`) —
   after cutaway this must point at RugBrain's own `/openapi.json`.

### 4.3 Proposed gates (all blocking unless noted)

| Gate | Job | Rules | Baseline file |
|---|---|---|---|
| `size-ratchet` | quality | modified file >500 lines → fail; allowlisted path may not grow | `scripts/size-baseline.json` (NEW, shrink-only) |
| `monkeypatch-ratchet` | quality | total monkeypatch occurrences may not exceed baseline; per-file may not grow | `scripts/monkeypatch-baseline.json` (extend antipattern baseline) |
| `coverage-ratchet` | quality | global cov may not drop below `max(60, baseline)`; diff-cover stays 60 | `scripts/coverage-baseline.txt` |
| `ruff` / `mypy` | quality | already blocking; add `ruff format --check` | — |
| `secret-scan` | security | gitleaks + trufflehog already; add `detect-secrets --baseline` for diffed files | `.secrets.baseline` |
| `eval-smoke` | quality | in-process harness, gates from `eval/baseline.json` | `eval/baseline.json` |
| `eval-nightly` | schedule | live harness, same gates | `eval/baseline.json` |
| `import-boundary` | decoupled | `.py` under `src/` must not match `^\s*(from|import)\s+(app\.|rmi_backend|backend\b)` except `support/compat.py`; also forbid `"app.` string literals outside `import_guard.py` | — |
| `make-test-parity` | quality | `make test` must run the same pytest invocation as CI | — |
| `makefile-guard` | quality | fail if `Makefile` `test:` target contains `placeholder` | — |

Implementation: extend `scripts/check-antipatterns.py` (already the ratchet engine and already wired into
CI) rather than adding a parallel tool. Add `scripts/size-ratchet.py` only if the check is materially
different.

---

## 5. Backend-RAG cutaway & severing

### 5.1 rmi-backend RAG leftovers (inventory, classified)

| Artifact | Lines | Direction | Classification |
|---|---|---|---|
| `app/rag/` (package: service, embeddings, ingest/, retrieval/, dedup/, ann_index, compat, scan_intel, intelligence) | 7,709 total | backend-owned RAG | **DELETE** (superseded by rugbrain) |
| `app/rag/retrieval/` | 2,072 | backend retrieval | **DELETE** |
| `app/rag/ingest/` | 991 | backend ingest | **DELETE** (move any unique knowledge-base content to rugbrain `ingest_sources/`) |
| `app/rag/ann_index.py` | 364 | backend ANN | **DELETE** (Qdrant native) |
| `app/rag/dedup/` | 345 | backend dedup | **DELETE** (rugbrain has `near_dup`/`ingestion_quality`) |
| `app/supabase_vector.py` | 731 | legacy vector store | **DELETE** after confirming zero live callers |
| `app/databus/rag_ingestion.py` | 579 | DataBus → RAG | **MOVE** to rugbrain ingestion if still needed; else **DELETE** |
| `app/api/v1/rag/search.py` | 234 | backend RAG API | **DELETE** (route removed once rugbrain is the only RAG) |
| `app/rag_agentic.py` | 647 | backend agentic RAG | **KEEP-AS-HTTP-CLIENT** wrapper then **DELETE** (should call `rugbrain_client`) |
| `app/rag_tool.py` | 255 | backend RAG tool | **KEEP-AS-HTTP-CLIENT** → `rugbrain_client` |
| `app/graph_rag.py` | 242 | backend graph RAG | **DELETE** or **MOVE** to rugbrain if graph retrieval is wanted |
| `app/rag_observability.py` | 172 | metrics | **DELETE** (rugbrain has `observability.py`/`metrics.py`) |
| `app/multimodal_rag.py`, `app/bundle_cluster_rag.py`, `app/streaming_rag.py`, `app/github_rag_feeder.py`, `app/databus/rag_news_realtime.py`, `app/core/health_rag.py`, `app/rag_metrics_api.py`, `app/agents/rag.py`, `app/osint/exploit_rag.py`, `app/wallet_memory/rag_labels.py` | — | mixed | classify per-caller: **MOVE** unique logic to rugbrain, else **DELETE** |
| `app/integrations/rugbrain_client.py` | 424 | backend → rugbrain HTTP | **KEEP-AS-HTTP-CLIENT** (this is the correct seam; ensure it is the *only* coupling) |

**Backend-side rule:** after cutaway, the backend must reach RugBrain only via
`app/integrations/rugbrain_client.py` (HTTP, key via `RMI_RAG_API_KEY`). Any remaining
`from app.rag import ...` is a bug.

### 5.2 rugbrain → backend dependencies (inventory, classified)

| Artifact | Line(s) | Backend call | Class |
|---|---|---|---|
| `firehose/__init__.py` | 71, 176, 327, 351, 518 | `RAG_API=http://localhost:8000/api/v1/rag` for dedup-check, ingest, dedup-mark, search | **REPLACE-WITH-DIRECT** — ingest to local Qdrant; this is the critical cut |
| `firehose/news.py` | 25, 73 | `/databus/fetch/news`, `/prediction_markets` | **KEEP-AS-FALLBACK** → direct exists (`fetch_news_direct`, `fetch_prediction_markets_direct`) |
| `firehose/twitter.py` | 20 | `/databus/fetch/ct_rundown` | **REPLACE-WITH-DIRECT** — **no fallback today**; must add `fetch_ct_rundown_direct` |
| `firehose/twitter.py` | 55, 76 | `/databus/fetch/scam_monitor`, `/social_metrics` | **KEEP-AS-FALLBACK** → `fetch_social_sentiment_direct` exists |
| `historical.py` | 32–33 | `BACKEND_URL/api/v1/rag/ingest` | **REPLACE-WITH-DIRECT** — write to local store |
| `system_health.py` | 44, 255 | `BACKEND_HEALTH_URL` probe | **KEEP** (report-only; must not be required) |
| `market_context.py` | (docstring) | *served by* rugbrain; upstreams are direct (DefiLlama, FNG, DexScreener, CoinPaprika) | **KEEP** — no backend client call |
| `scanner_feedback.py` | — | *receives* scanner results | **KEEP** (inbound; backend → rugbrain) |
| `scanner_subscriber.py`, `scanner_poller.py` | — | Redis streams from scanner | **KEEP** with degraded mode if stream absent |
| `mempool_scanner.py` | 85, 138 | direct Etherscan/Helius | **KEEP** |
| `dashboard.py`, `evaluation.py`, `eval/retrieval_eval.py` | 8012/8012 | rugbrain's own API | **KEEP** (self, not backend) |
| `import_guard.py` | `_APP_MODULES` (24) | stub `app.*` in `sys.modules` | **DELETE** after P4/P5 land |

`firehose/direct_sources.py` (158 lines, 3 fetchers: `fetch_social_sentiment_direct`,
`fetch_news_direct`, `fetch_prediction_markets_direct`) is the established standalone pattern — follow it
for every new direct source. Doc contract: shapes match the DataBus-normalized forms so fallback is
drop-in.

### 5.3 Success criterion

> **RugBrain runs standalone with rmi-backend down.** Concretely: with `BACKEND_URL` pointed at a closed
> port, `make api-dev` boots, `/health` returns ok, the firehose completes one ingest cycle into local
> Qdrant for news/prediction/ct-rundown/social, and a search returns results. Verified by
> `tests/integration/test_standalone_firehose.py` (new) in the nightly job. `import_guard.py` no longer
> injects any `app.*` module because none are imported.

---

## 6. Phased execution plan

Each work package is agent-sized: one repo, a bounded file set, explicit verification. Ordered by
dependency. Rollback is "revert the commit" unless stated otherwise.

### Phase 0 — Baseline (repo: rugbrain) — 1 package
- **P0.1** Add `docs/MODERNIZATION.md` (this doc); record `size-baseline.json` from current `wc -l`;
  record monkeypatch/coverage baselines. **Verify:** baselines parse and match working tree.

### Phase 1 — Ratchet infrastructure (repo: rugbrain, CI) — 3 packages
- **P1.1** Extend `scripts/check-antipatterns.py` with `size-ratchet` (shrink-only, HARD-FAIL >500 on
  modified files) and `monkeypatch-ratchet` (decrease-only). Wire into `commitlint`/`quality` job.
  **Risk:** false positives on legitimately large new files — mitigate with explicit PR-body waiver label.
- **P1.2** Fix `Makefile` `test:` to mirror CI pytest invocation; add `makefile-guard` check.
- **P1.3** Delete or neutralize stale `.github/workflows/ci.yml`. **Verify:** no second pytest path.
- **Rollback:** revert; ratchets are additive.

### Phase 2 — Eval harness (repo: rugbrain) — 3 packages
- **P2.1** Extend `eval/golden_set.jsonl` to >=150 rows with stable `gold_chunk_ids`; generate
  `eval/baseline.json` from current live run. **Verify:** harness reproduces `docs/eval-baseline-2026-08.md`
  within tolerance.
- **P2.2** Add `eval/harness.py` (reuse `ndcg.py`, `metrics.py`) + `eval/gates.py`. Unit-test both.
- **P2.3** Add `eval-smoke` PR job (in-process fixture retriever) and `eval-nightly` live job.
  **Risk:** live Qdrant instability → nightly retries once, reports inconclusive rather than red.
- **Rollback:** remove jobs; harness is additive.

### Phase 3 — Retrieval de-god (repo: rugbrain) — 5 packages
- **P3.1** Extract `retrieval/fusion.py` (`_hybrid_fuse`, `bias_toward_code_chunks`, `_is_code_result`)
  from `search/engine.py`; re-export for compat.
- **P3.2** Split `three_pillar_search()` (944–1597) into `pillar_scam.py`, `pillar_coding.py`,
  `pillar_market.py` + thin orchestrator. **Highest risk** — full retrieval test suite + eval-smoke gate
  required. **Verify:** eval-smoke deltas within gate; `tests/test_search*.py` green.
- **P3.3** Split `QdrantVectorStore` (390–869) into `stores/qdrant_store.py` + `stores/filters.py` +
  `stores/collections.py` + `stores/bm25.py`.
- **P3.4** Move `search/filters.py`, `search/rerank.py`, `search/query_transform.py` → `retrieval/`.
- **P3.5** Remove monkeypatch coupling in `routes/search.py`/`routes/analyze.py` via injected
  dependencies; reduce monkeypatch baseline.
- **Rollback:** retrieval split is high-risk; each package is a separate commit so a failing eval reverts
  one package, not the phase.

### Phase 4 — Firehose standalone cutaway (repo: rugbrain) — 4 packages
- **P4.1** Replace `firehose/__init__.py` `RAG_API` ingest/dedup/search calls with direct local store
  calls (Qdrant + redis dedup). Delete `RAG_API` constant.
- **P4.2** Add `fetch_ct_rundown_direct` to `direct_sources.py`; wire fallback in `firehose/twitter.py`.
- **P4.3** Convert `historical.py` to write directly to the local store; remove `RAG_INGEST_URL`.
- **P4.4** Add `tests/integration/test_standalone_firehose.py` (backend URLs → closed port).
- **Verify (phase exit):** §5.3 success criterion. **Risk:** ingest duplication if both paths run during
  rollout — gate direct path behind `RAG_DIRECT_INGEST=true`, default true only after one nightly green.

### Phase 5 — Alerts de-god (repo: rugbrain) — 2 packages
- **P5.1** Diff `realtime_alerts.py` (1655) vs `alerts.py` (1078); extract shared logic into
  `alerts/engine.py`, split sources into `alerts/sources/*.py`. **Verify:** alert tests + subscriber tests
  green; no duplicate delivery.
- **P5.2** Move `main.py` lifespan poller startup (lines 210–395) into `support/pollers.py` registry;
  `main.py` drops under 200 lines.

### Phase 6 — Content/ingest de-god (repo: rugbrain) — 3 packages
- **P6.1** `app_chunking.py` (1046) → `ingestion/chunking/{app,parent_child,tree_sitter,solidity}.py`.
- **P6.2** `news_rating.py` + `news_ranking.py` + `news_clustering.py` → `ingestion/news/*`.
- **P6.3** `fear_index.py` (773, 31 fn) and `prompt_compression.py` (734, 23 fn) → `support/` split.
- **Verify each:** module-specific tests green; size ratchet passes; no file >500.

### Phase 7 — Backend decommission (repo: rmi-backend) — 4 packages
- **P7.1** Grep callers of `app/rag/*`, `supabase_vector.py`, `rag_*`, `graph_rag.py`; route every live
  caller through `app/integrations/rugbrain_client.py`.
- **P7.2** Move any still-needed knowledge-base content/CSV from `app/rag/ingest/` to rugbrain
  `ingest_sources/`; else delete.
- **P7.3** Delete `app/rag/` (7709 lines), `app/supabase_vector.py`, `app/api/v1/rag/search.py`, backend
  RAG jobs; update `.forgejo` nightly fuzz `SCHEMA_URL` to RugBrain's own OpenAPI.
- **P7.4** Remove `app/integrations/rugbrain_client.py`'s direct-Qdrant fallback (backend must not touch
  Qdrant); keep the HTTP client.
- **Verify:** backend boots with rugbrain reachable and unreachable paths both handled; no `app.rag`
  imports remain (`rg 'from app\.rag|import app\.rag'` empty).

### Phase 8 — Entrypoint cleanup (repo: rugbrain) — 2 packages
- **P8.1** Delete `import_guard.py`; remove `install_import_guard()` from `main.py`; confirm zero `app.*`
  imports (CI `decoupled` + new `import-boundary`).
- **P8.2** Rename `compat.py` → `support/compat.py`; update imports; add module docstring marking it as
  the only legacy-compat seam.
- **Verify:** `import rugbrain.main` succeeds with a clean venv (`app` not installed); full test suite +
  eval-smoke green.

**Phase count: 9** (P0–P8) · **Work packages: 27**.

---

## 7. Open questions / [REVIEW]

1. **[REVIEW] `realtime_alerts.py` vs `alerts.py`** — 1655 vs 1078 lines with overlapping names. Are they
   intentionally separate (stream vs batch) or accidental duplication? Must be answered before P5.1;
   if duplicate, consolidation is a bigger win than splitting.
2. **[REVIEW] `app/rag/ingest/_knowledge_bases/*`** — some may hold curated knowledge not yet mirrored in
   rugbrain `ingest_sources/`. Requires a content diff before deleting (P7.2). Do not blind-delete.
3. **[REVIEW] `supabase_vector.py` (731 lines)** — confirm zero live callers. Grep shows it is not
   imported by rugbrain; backend callers unknown.
4. **[REVIEW] Golden corpus stable IDs** — are Qdrant point IDs stable across re-ingestion? If not, the
   harness must resolve gold chunks by content hash instead. Decide in P2.1.
5. **[REVIEW] `eval/baseline.json` source of truth** — `docs/eval-baseline-2026-08.md` is prose. Need a
   machine-readable baseline; confirm the August numbers were produced with the current corpus.
6. **[REVIEW] `.github` vs `.forgejo` ownership** — is GitHub a mirror that must stay functional? If yes,
   P1.3 becomes "sync from forgejo" rather than "delete".
7. **[REVIEW] Direct-CT-rundown source** — `fetch_ct_rundown_direct` needs an upstream. The backend's
   `app/databus` aggregator likely wraps Nitter/X APIs; the standalone replacement must be chosen
   (RSS/Nitter/paywalled X). Blocker for P4.2.
8. **[REVIEW] Cost of nightly live eval** — embedding + Qdrant load per nightly run across 150 queries.
   Confirm budget; if too high, sample 50 queries nightly and full corpus weekly.
9. **[REVIEW] `monitors/` scale** — 10 monitors each <500 today except `social_sentiment.py` (906). Decide
   whether monitors belong in `rugbrain` or a sibling service as the repo grows.
10. **[REVIEW] mypy strict + new package moves** — moving files changes module paths in test monkeypatch
    targets and `[tool.mutmut]`/`[tool.coverage]` scopes. Each phase must update those configs in the same
    commit.

---

## Appendix A — God-file list (top 25, exact counts)

```
1771  src/rugbrain/search/engine.py
1655  src/rugbrain/realtime_alerts.py
1078  src/rugbrain/news_rating.py
1078  src/rugbrain/alerts.py
1076  src/rugbrain/mcp_bridge.py
1046  src/rugbrain/app_chunking.py
 935  src/rugbrain/guardrails.py
 906  src/rugbrain/monitors/social_sentiment.py
 878  src/rugbrain/liquidation_heatmap.py
 874  src/rugbrain/qdrant_store.py
 818  src/rugbrain/crypto_embeddings.py
 811  src/rugbrain/main.py
 777  src/rugbrain/firehose/__init__.py
 773  src/rugbrain/fear_index.py
 734  src/rugbrain/prompt_compression.py
 720  src/rugbrain/bundle_cluster.py
 719  src/rugbrain/splade_bm25.py
 671  src/rugbrain/youtube_ingester.py
 669  src/rugbrain/sentiment_engine.py
 657  src/rugbrain/contract_analysis.py
 646  src/rugbrain/routes/search.py
 637  src/rugbrain/web_crawler.py
 632  src/rugbrain/eval/retrieval_eval.py
 615  src/rugbrain/ingest_sources/incidents.py
 603  src/rugbrain/firehose/market_sources.py
```

## Appendix B — Backend `app.*` stub surface to delete (import_guard `_APP_MODULES`)

```
app.ann_index            app.domains.databus.rag_engine    app.query_transform
app.confidence           app.domains.databus.social_intel  app.rag.embeddings
app.core.redis           app.domains.databus.x_intel       app.rag.incremental_indexer
app.cross_chain          app.domains.scanners.unified_scanner  app.rag.parent_retriever
app.cross_encoder_reranker  app.entity_extraction           app.rag_agentic
app.deployer_reputation  app.hallucination_guard            app.rate_limiter
app.domains.databus.news_intel  app.investigation_narratives  app.scam_sources
app.domains.databus.news_provider  app.knowledge_graph       app.semantic_cache
                         app.mmr_dedup
```

## Appendix C — Evidence commands

```bash
# inventory / line counts
find src/rugbrain -name '*.py' -exec wc -l {} + | sort -rn
# top-level function+class counts
rg -c '^(async )?def |^class ' src/rugbrain/<file>
# backend leftovers
ssh <internal-host> 'cd <repo-root> && find app -maxdepth 2 -iname "*rag*"'
# rugbrain backend coupling
rg -n 'from app\.|RAG_API|localhost:8000' src/rugbrain -g '*.py'
# existing ratchet baseline
cat scripts/antipattern-baseline.json
```

# RAG Retrieval Eval Baseline — 2026-08

**Date:** 2026-08-16
**Harness:** `python -m rugbrain.eval.retrieval_eval` (`src/rugbrain/eval/retrieval_eval.py`)
**Golden set:** `src/rugbrain/eval/golden_set.jsonl` (**44 queries**)
**Runner modes:** live HTTP endpoint `http://localhost:8012/api/v1/search` (X-API-Key auth) · `--inprocess` engine path · `--rerank` delta
**Top-k:** 5
**Read-only:** GET `/api/v1/search` only — no Qdrant/Redis writes.

> **STATUS: BASELINE BLOCKED — embedder saturated (Infinity inference hangs).**
> Real numbers NOT captured on 2026-08-16. Commands + expected runtime for a
> clean re-run at the bottom. Do not re-run while `POST /embeddings` on
> `infinity-rag` (127.0.0.1:8001) takes >60s per call.

## What is measured

Retrieval quality + latency of the live RAG search path against a curated
golden set of **44** real crypto queries. Each golden line is `{query,
gold_chunks, collection?, expected_answer?}` where a gold chunk is a
distinctive phrase that must appear in a retrieved document's content for the
document to count as relevant (doc ids are UUIDs and not stable across
ingests, so content matching is the ground truth).

Per-query metrics (all implemented in the harness):

| Metric | Definition |
|--------|-----------|
| `hit@1` | the top-1 result contains ≥ 1 gold chunk. |
| `hit@5` | any of the top-5 results contains ≥ 1 gold chunk. |
| `recall@5` | fraction of the query's gold chunks surfaced in the top-5. |
| `precision@5` | fraction of the top-5 results that contain ≥ 1 gold chunk. |
| `NDCG@5` | ranking quality with log2 position discount over binary per-rank relevance (gain 1.0 when a top-5 doc contains ≥ 1 gold chunk, else 0.0; ideal DCG from the best ordering of the actual gains). |
| `MRR` | reciprocal rank of the first relevant result. |
| latency | end-to-end per-query (HTTP round-trip in endpoint mode, engine call in inprocess mode), reported as p50 / p95 / mean. |

Golden-set composition (44 queries):

| collection | count |
|-----------|------:|
| coding_knowledge | 20 |
| defi_exploits | 13 |
| threat_intel_v2 | 4 |
| (default) | 4 |
| solana_patterns | 3 |

## Baseline numbers — BLOCKED (no live numbers on 2026-08-16)

| Metric | Value |
|--------|-------|
| Queries | 44 |
| Errors | — (not run) |
| hit@1 | — |
| hit@5 | — |
| recall@5 | — |
| precision@5 | — |
| NDCG@5 | — |
| MRR | — |
| p50 latency | — |
| p95 latency | — |

### Why it's blocked — probe evidence

| Probe | Endpoint | Result |
|-------|----------|--------|
| embedder health | `GET http://127.0.0.1:8001/health` | 200 OK, 12 ms (HTTP layer healthy — but liveness only, does not exercise model inference) |
| embedder models | `GET http://127.0.0.1:8001/models` | 200 OK, models served: `BAAI/bge-m3` (reranking served by Qwen3-Reranker-8B on :8003, not Infinity) |
| **real embedding** | `POST /embeddings` (bge-m3, 1 tiny input) | **timed out at 30s** (no response, no log line — inference worker/queue saturated) |
| **live search** | `GET /api/v1/search?q=how does Uniswap V3 compute swap fees&limit=5` (valid key) | **timed out at 90s** (HTTP 000, no response) |
| **live search (confirm)** | `GET /api/v1/search?q=reentrancy guard pattern solidity&limit=5` (valid key) | **timed out at 15s** (HTTP 000, no response) |
| unauthenticated | `GET /api/v1/search` (no key) | 401 in 5 ms (auth layer responsive) |

Container status: `infinity-rag` Up 5 hours (healthy), `rmi-rag-api` Up 7
days (healthy). API `/health` reports qdrant=true, redis=true. The hang is
specifically the **embedding inference step**: the API accepts the search
request (never logs a completion), and direct `POST /embeddings` to Infinity
hangs even for a one-line input. This matches the earlier failure mode in the
old 21-query baseline where `infinity-rag` returned 503
`embedding_unavailable` — now it hangs instead of failing fast.

Per the operator instruction **do not hammer a saturated embedder**, no
further probes or eval runs were attempted. `--inprocess` was deliberately NOT
run either: `rugbrain.search.engine.search_multi_collection` calls
`get_embedder()` → `INFINITY_URL` (the same saturated Infinity), so it would
hang identically on query #1.

### Harness sanity checks performed (all pass)

```
.venv/bin/python -c "import rugbrain.eval.retrieval_eval"          # import OK
.venv/bin/python -m rugbrain.eval.retrieval_eval --help            # CLI OK
load_golden_set('src/rugbrain/eval/golden_set.jsonl')              # 44 queries parse OK
rugbrain.eval.ndcg (ndcg_from_gains, mrr_from_gains, hit/recall/precision_at_k)  # importable
```

## Historical note (superseded — old 21-query run, 2026-08-16 earlier)

An earlier baseline doc captured a **21-query** run against the previous
golden set with the previous harness (no NDCG/MRR, no `--rerank`). Those
numbers are **not comparable** to the current 44-query set and are kept here
only as historical context:

| Metric | old 21-query value |
|--------|--------------------|
| hit@1 | 0.809 |
| hit@5 | 1.000 |
| recall@5 | 0.984 |
| precision@5 | 0.409 |
| p50 / p95 latency | 138 / 202 ms |

The old run also surfaced: cache warm-up roughly halves p50 (276→138 ms), and
the reranker contribution / NDCG / MRR were flagged as NOT computed. All three
gaps are now implemented in the harness (NDCG@5, MRR, `--rerank` delta) and
are ready for the first clean 44-query run.

## How to re-run (when the embedder is healthy)

Precondition: `POST http://127.0.0.1:8001/embeddings` (bge-m3) answers in
well under 60s for a single input. Do not start while the embedder is
saturated.

```bash
cd /srv/work/repos/rmi-rag

# Baseline (rerank=false), live endpoint — requires RMI_RAG_API_KEY in env
# (set via: set -a; source .env.rag; set +a)
.venv/bin/python -m rugbrain.eval.retrieval_eval \
  --top-k 5 --endpoint http://localhost:8012 --timeout 180

# Rerank delta (rerank=false + rerank=true, prints with/without table) —
# doubles the runtime
.venv/bin/python -m rugbrain.eval.retrieval_eval \
  --top-k 5 --endpoint http://localhost:8012 --timeout 180 --rerank

# JSON output for tooling
.venv/bin/python -m rugbrain.eval.retrieval_eval \
  --top-k 5 --endpoint http://localhost:8012 --timeout 180 --json

# In-process engine (no HTTP; same embedder dependency — needs Infinity +
# Qdrant/Redis reachable from the eval process)
.venv/bin/python -m rugbrain.eval.retrieval_eval --top-k 5 --inprocess
```

### Expected runtime (from the old 21-query run + harness shape)

* 44 queries × ~150–300 ms/query (warm cache) ≈ **1–3 min** baseline.
* With `--rerank`: baseline + rerank pass ≈ **2× baseline** (each query hits
  the endpoint twice; rerank adds a cross-encoder scoring pass over top-20
  candidates) — expect ≈ **2–6 min** total when healthy.
* If the embedder is degraded-but-alive (per-query 5–60s), a full 44-query run
  plus rerank could stretch to **30–90 min** — the operator's upper bound.
  Use `--timeout 180` (default) so a single stuck query fails fast instead of
  hanging the whole run; the harness counts per-query errors rather than
  aborting.

### What to record on the next run

Aggregate table: queries (44), errors, hit@1, hit@5, recall@5, precision@5,
NDCG@5, MRR, p50/p95/mean latency. If `--rerank` ran, also the delta table
(hit@1 / NDCG@5 / MRR / hit@5 / recall@5 / precision@5 with vs without) and
whether `reranker_applied` was true (the endpoint degrades silently to raw
vector ranking when the reranker backend is unavailable).

## Prior gaps — all closed in the current harness

* ~~NDCG@k / MRR not reported~~ → now reported (`rugbrain.eval.ndcg`,
  `mean_ndcg_at_k`, `mean_mrr`).
* ~~Reranker contribution not measured~~ → `--rerank` runs the eval twice and
  prints the delta, surfacing `reranker_applied` from the endpoint.
* Generation/faithfulness — still out of scope by design (retrieval-only);
  the RAG-quality half lives in `rugbrain.eval.run_eval` + `metrics`.
* Chain-data fallback (`chain_data=true`, `/api/v1/rag/chain/query`) — still
  not exercised by this golden set.

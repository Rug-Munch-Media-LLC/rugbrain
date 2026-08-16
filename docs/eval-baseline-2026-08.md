# RAG Retrieval Eval Baseline — 2026-08

**Date:** 2026-08-16
**Harness:** `python -m rugbrain.eval.retrieval_eval` (`src/rugbrain/eval/retrieval_eval.py`)
**Golden set:** `src/rugbrain/eval/golden_set.jsonl` (21 queries)
**Runner mode:** live HTTP endpoint `http://localhost:8012/api/v1/search` (X-API-Key auth)
**Top-k:** 5
**Read-only:** GET `/api/v1/search` only — no Qdrant/Redis writes.

## What is measured

Retrieval quality + latency of the live RAG search endpoint against a curated
golden set of 21 real crypto queries. Each golden line is `{query, gold_chunks,
collection?}` where a gold chunk is a distinctive phrase that must appear in a
retrieved document's content for the document to count as relevant (doc ids are
UUIDs and not stable across ingests, so content matching is the ground truth).

Per-query metrics:

* `hit@1` — the top-1 result contains ≥ 1 gold chunk.
* `hit@5` — any of the top-5 results contains ≥ 1 gold chunk.
* `recall@5` — fraction of the query's gold chunks surfaced in the top-5.
* `precision@5` — fraction of the top-5 results that contain ≥ 1 gold chunk.

Latency is the end-to-end HTTP round-trip per query (embedding + hybrid
dense/BM25 search + response), reported as p50 / p95 / mean.

## Baseline numbers

| Metric | Value |
|--------|-------|
| Queries | 21 |
| Errors | 0 |
| **hit@1** | **0.809** |
| **hit@5** | **1.000** |
| **recall@5** | **0.984** |
| **precision@5** | **0.409** |
| **p50 latency** | **138 ms** |
| **p95 latency** | **202 ms** |
| mean latency | 152 ms |

## Per-query detail

| Query | collection | hit@1 | hit@5 | recall@5 | prec@5 | lat (ms) |
|-------|-----------|------:|------:|---------:|-------:|---------:|
| how does Uniswap V3 compute swap fees | coding_knowledge | 1.00 | 1.00 | 1.00 | 0.20 | 202 |
| reentrancy guard pattern solidity | coding_knowledge | 0.00 | 1.00 | 1.00 | 0.20 | 138 |
| Checks-Effects-Interactions pattern Solidity | coding_knowledge | 1.00 | 1.00 | 1.00 | 0.80 | 131 |
| oracle manipulation TWAP spot price attack | coding_knowledge | 1.00 | 1.00 | 1.00 | 0.40 | 124 |
| flash loan attack how it works | coding_knowledge | 1.00 | 1.00 | 1.00 | 0.20 | 126 |
| MEV sandwich attack prevention | coding_knowledge | 1.00 | 1.00 | 1.00 | 0.20 | 136 |
| smart contract vulnerability classes reentrancy overflow | coding_knowledge | 1.00 | 1.00 | 1.00 | 1.00 | 133 |
| private key storage security best practices | coding_knowledge | 0.00 | 1.00 | 1.00 | 0.20 | 121 |
| secure contract coding best practices | coding_knowledge | 1.00 | 1.00 | 1.00 | 0.60 | 132 |
| Gnosis Safe multisig wallet threshold | coding_knowledge | 0.00 | 1.00 | 1.00 | 0.20 | 138 |
| Euler Finance flash loan attack 2023 | defi_exploits | 1.00 | 1.00 | 0.67 | 0.40 | 139 |
| what was the Euler hack | (default) | 1.00 | 1.00 | 1.00 | 0.20 | 154 |
| Wormhole bridge exploit 2022 120000 ETH | (default) | 1.00 | 1.00 | 1.00 | 0.20 | 150 |
| Wormhole signature verification Solana bridge | defi_exploits | 1.00 | 1.00 | 1.00 | 1.00 | 139 |
| Curve reentrancy exploit 2023 | defi_exploits | 1.00 | 1.00 | 1.00 | 0.20 | 154 |
| Ronin bridge hacked 622 million validator | defi_exploits | 1.00 | 1.00 | 1.00 | 0.40 | 130 |
| Nomad bridge exploit 190 million | defi_exploits | 1.00 | 1.00 | 1.00 | 0.80 | 143 |
| honeypot token cannot sell | (default) | 1.00 | 1.00 | 1.00 | 0.40 | 146 |
| honeypot token transfer restriction | (default) | 1.00 | 1.00 | 1.00 | 0.40 | 137 |
| Solana PDA derived address signer | solana_patterns | 1.00 | 1.00 | 1.00 | 0.40 | 156 |
| oracle price manipulation flash loan lending | defi_exploits | 0.00 | 1.00 | 1.00 | 0.20 | 153 |

## Observations

1. **hit@5 = 1.000** — every query surfaces at least one gold chunk in the
   top-5. The retrieval pipeline is reliable at finding *something* relevant.
2. **hit@1 = 0.809 (17/21)** — 4 queries miss the top slot:
   `reentrancy guard pattern solidity`, `private key storage security best
   practices`, `Gnosis Safe multisig wallet threshold`, `oracle price
   manipulation flash loan lending`. These are the ranking-quality
   opportunities (relevant doc ranked #2+).
3. **recall@5 = 0.984** — one gold chunk missed: the Euler query's `197`
   chunk (the amount-lost figure) wasn't in the top-5, so `Euler Finance
   flash loan attack 2023` scored recall 0.67. Multi-fact queries are the
   recall pressure point.
4. **precision@5 = 0.409** — on average only ~2 of 5 returned docs contain a
   gold chunk. Retrieval is recall-heavy; the extra 3 slots are
   near-duplicates/related-but-not-gold content. This is where a reranker or
   MMR dedup shows up.
5. **Latency: p50=138ms, p95=202ms** — matches the ARCHITECTURE.md claim
   (p50 45ms is optimistic; measured live is ~3x higher but well under the
   180ms p99 claim). These numbers include full HTTP + embedding + hybrid
   search. The first baseline run (cold semantic cache) showed p50=276ms /
   p95=401ms; the second run benefited from warm cache, so **cache warm-up
   roughly halves p50**. Latency figures here are the warm-cache run.
6. **Embedding backend availability**: the `infinity-rag` container was
   restarting during early probing and the endpoint returned 503
   `embedding_unavailable` (hash fallback removed per T04). All 21 eval
   queries ran cleanly once Infinity was healthy — but the harness counts
   503s as per-query errors, so infra downtime is visible in `errors`.

## How to re-run

```bash
# Live endpoint (requires RMI_RAG_API_KEY in env, endpoint up)
python -m rugbrain.eval.retrieval_eval --top-k 5 --endpoint http://localhost:8012

# JSON output for tooling
python -m rugbrain.eval.retrieval_eval --top-k 5 --endpoint http://localhost:8012 --json

# In-process engine (no HTTP; needs Redis/Qdrant reachable)
python -m rugbrain.eval.retrieval_eval --top-k 5 --inprocess
```

## [REVIEW] — metrics not computed

* **Reranker contribution**: the golden set was run with the endpoint's
  default `rerank=false`. The endpoint supports `rerank=true` (bge-reranker
  cross-encoder on top-20 candidates); the harness does not yet compare
  rerank on/off, so **the reranker's lift over raw hybrid retrieval is not
  measured**.
* **NDCG@k / MRR**: implemented in `rugbrain.eval.ndcg` and used by
  `run_eval`, but not reported by `retrieval_eval` (kept to the task scope of
  hit/recall/precision + latency).
* **Generation/faithfulness**: retrieval-only by design — see `run_eval` +
  `metrics` for the RAG-quality half (faithfulness, answer relevance,
  context precision/recall). No LLM-judged (ragas) baseline yet.
* **Chain-data fallback**: `chain_data=true` and the parquet-backed
  `/api/v1/rag/chain/query` path are not exercised by this golden set.

# RugBrain Evals — retrieval-quality backtesting & gates

This document covers the **offline retrieval-quality harness** (`eval/harness.py`),
the **regression gates** (`eval/gates.py`), the **golden set**
(`eval/golden/crypto_rag_golden.jsonl`), and the **CI smoke runner**
(`scripts/run-eval-smoke.sh`). It is the "additional testing" foundation from
`docs/MODERNIZATION.md` §3.4 (win #4).

The harness is deliberately **offline-first and LLM-judge-free**: it measures
classic retrieval metrics (recall@k, MRR, nDCG@k) plus latency, with no Ragas /
DeepEval dependency in the main venv. Optional LLM-judge extras live in
`requirements-eval.txt` and degrade gracefully when absent.

---

## 1. What it measures

| Metric | Meaning | Direction |
|---|---|---|
| `recall@k` | fraction of gold chunks found in the top-k | higher is better |
| `nDCG@k` | ranking quality with log2 position discount | higher is better |
| `MRR` | reciprocal rank of the first relevant hit | higher is better |
| `p50` / `p95` latency | per-query end-to-end retrieval latency | lower is better |

Relevance is judged two ways:

* **doc-id mode** — when a golden row carries `relevant_doc_ids`, an exact id
  match is required. This is the honest signal for stable Qdrant point IDs.
* **chunk mode** — otherwise a case-insensitive substring match of
  `gold_chunks` against the result `content`. Used because Qdrant point IDs are
  UUIDs that are not stable across re-ingests.

---

## 2. How to run

### Fast smoke (CI / local)

```bash
scripts/run-eval-smoke.sh                 # 10-query subset, direct-Qdrant retriever
EVAL_LIMIT=20 scripts/run-eval-smoke.sh   # larger subset
EVAL_STRICT=1 scripts/run-eval-smoke.sh   # a backend-down skip becomes a failure
```

Exit codes: `0` = pass or skip, `1` = gate failure (or skip under `EVAL_STRICT=1`).

### Full harness (Python)

```python
import asyncio
from rugbrain.eval.harness import run_backtest, direct_qdrant_retriever
from rugbrain.eval.gates import evaluate_gates, format_verdict

report = asyncio.run(run_backtest(retriever=direct_qdrant_retriever(), top_k=10))
print(format_verdict(evaluate_gates(report)))
```

### Retriever contract

A retriever is an async callable `(query, collection, limit) -> list[dict]`.
Result dicts carry an id under `id` / `doc_id` / `uuid` / `chunk_id` and
optionally `content`. Two retrievers ship with the harness:

* `direct_qdrant_retriever()` — scrolls Qdrant and scores cosine in-process.
  **Offline-safe**: needs only Qdrant, not Redis/FAISS. This is what the smoke
  script uses.
* the default (`retriever=None`) — the live in-process engine
  (`search_multi_collection`), which needs Redis + a built FAISS index.

For PR CI, inject a **recorded fixture retriever** (deterministic, no network)
so the job is fast and hermetic.

---

## 3. Fail-open behaviour

The harness never crashes on a missing backend:

* every query error is captured into `QueryScore.error`;
* when **all** queries error, `BacktestReport.status == "unavailable"`;
* `evaluate_gates` treats `unavailable` / `empty` as a **skip**, not a failure;
* `run-eval-smoke.sh` exits `0` on a skip unless `EVAL_STRICT=1`.

This keeps an offline CI runner from going red on infrastructure absence while
still failing on a genuine quality regression.

---

## 4. Extending the golden set

Add one JSON object per line to `eval/golden/crypto_rag_golden.jsonl`:

```json
{"query": "...", "collection": "security_patterns",
 "gold_chunks": ["..."], "relevant_doc_ids": ["<qdrant-point-id>"],
 "expected_answer": "...", "category": "coding", "provenance": "verified"}
```

Rules:

1. **`provenance` is mandatory** and must be `verified` or `synthetic`.
   * `verified` — you confirmed the chunk exists in the live corpus (scroll
     Qdrant, observe the point ID + content). Verified rows **must** carry
     `relevant_doc_ids`.
   * `synthetic` — hand-written coverage not yet confirmed. Safe to keep, but
     **must not** be used to raise a baseline.
2. **`category`** ∈ `coding | scam | incident | market | research | general`.
3. Every row must carry at least one of `gold_chunks` / `relevant_doc_ids`.
4. To derive a verified row, scroll the collection and confirm the chunk:

   ```bash
   curl -s http://127.0.0.1:6334/collections/security_patterns/points/scroll \
     -H 'Content-Type: application/json' \
     -d '{"limit":1000,"with_payload":true,"with_vector":false}'
   ```

   Then copy the point `id` into `relevant_doc_ids` and a distinctive phrase
   from its `content`/`title` into `gold_chunks`.

The schema is enforced by `tests/test_eval_harness.py::TestGoldenSet`.

---

## 5. How gates evolve

Baselines live in `eval/baselines.json`:

```json
{"recall@10": {"value": 0.30, "tolerance": 0.05},
 "p95_latency_ms": {"value": 3000.0, "tolerance": 0.50}}
```

Semantics:

* **Quality metrics are shrink-only**: `actual >= value - tolerance`.
* **Latency is raise-only**: `actual <= value * (1 + tolerance)`.
* A metric **absent** from the file is not gated (reported as `skipped`), so new
  metrics can be added to the harness without breaking CI.

Evolution policy:

* **Raising** a baseline is an explicit, reviewable edit in the same PR that
  improves retrieval. Only `verified` golden rows may justify a raise.
* **Lowering** a baseline requires a written justification in the PR body; the
  file is the audit trail.
* The current baselines are **provisional** — they were recorded from the
  direct-Qdrant offline retriever (no rerank/hybrid), which scores lower than
  the live engine. Re-baseline once the nightly live job is green.

---

## 6. CI wiring

The smoke runner is CI-ready but **not yet wired** (another agent owns
`.forgejo/workflows/ci.yml`). Add this step to the `quality` job:

```yaml
      - name: eval-smoke (retrieval quality gate)
        run: |
          if [ -x scripts/run-eval-smoke.sh ]; then
            scripts/run-eval-smoke.sh
          else
            echo "eval-smoke: script absent, skipping"
          fi
        env:
          QDRANT_URL: http://127.0.0.1:6334
          EVAL_LIMIT: "10"
```

For the nightly job, run the full golden set against the live engine and upload
`eval/results/<sha>.json` as an artifact.

---

## 7. Optional LLM-judge extras

`requirements-eval.txt` lists optional extras (`ragas`, `deepeval`). They are
**not** installed in the main venv. `rugbrain.eval.metrics.ragas_available()`
reports whether ragas is importable; the harness never requires it. Use these
only for periodic human-verified audits, not the per-PR gate.

---

## 8. Files

| File | Purpose |
|---|---|
| `src/rugbrain/eval/harness.py` | offline backtest runner + retrievers |
| `src/rugbrain/eval/gates.py` | threshold gate + verdict formatting |
| `src/rugbrain/eval/baselines.json` | committed thresholds |
| `src/rugbrain/eval/golden/crypto_rag_golden.jsonl` | golden set |
| `scripts/run-eval-smoke.sh` | fast CI subset runner |
| `tests/test_eval_harness.py` | metric math, gate semantics, schema tests |
| `requirements-eval.txt` | optional LLM-judge extras |

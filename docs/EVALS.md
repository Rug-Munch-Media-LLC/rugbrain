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

### Nightly full run

```bash
scripts/run-eval-nightly.sh                 # all golden rows, full baseline
EVAL_STRICT=1 scripts/run-eval-nightly.sh   # a backend-down skip becomes a failure
```

The nightly runner scores the **entire** golden set against
`eval/baselines.full.json`, appends a JSON record to `eval/history.jsonl`
(timestamp, git sha, metrics, gate verdict), and exits non-zero on gate
failure. A pre-flight `curl` on `/healthz` makes a down Qdrant SKIP
immediately (exit 0) instead of waiting for per-query timeouts.

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
     `relevant_doc_ids` **and** a `provenance_note` recording how/when it was
     confirmed. Verified rows are scored in **doc-id mode** (exact point-id
     match), which is stricter than chunk-substring mode.
   * `synthetic` — hand-written coverage not yet confirmed. Safe to keep, but
     **must not** be used to raise a baseline.
   * As of 2026-09-19 all 41 rows are `verified`; the 19 previously-synthetic
     rows were confirmed by scrolling live Qdrant and copying the point id +
     a distinctive content phrase. Two rows were re-pointed to the correct
     collection (`known_scams`) after the original collection had no match.
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

Baselines are **split by query set** so a 10-query smoke run is never gated
against a full-set threshold (the two sets have different metric
distributions):

| File | Query set | Used by |
|---|---|---|
| `eval/baselines.smoke.json` | first 10 golden rows | `scripts/run-eval-smoke.sh` |
| `eval/baselines.full.json` | all 41 golden rows | `scripts/run-eval-nightly.sh` |

`evaluate_gates(report, limit=...)` selects the file automatically via
`eval/baseline_select.py`: `limit is None` (or `limit >= total`) → full,
otherwise smoke. Callers that pass an explicit `baselines=` path bypass the
selection.

```json
{"recall@10": {"value": 0.39, "tolerance": 0.05},
 "p95_latency_ms": {"value": 20000.0, "tolerance": 0.50}}
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
* **Measured 2026-09-19** (direct-Qdrant, all 41 rows verified, doc-id mode):
  full set recall@5=0.2927 / recall@10=0.3902 / nDCG@5=0.2364 / nDCG@10=0.2676
  / MRR=0.2301 / p50=11993.8ms / p95=19043.4ms; smoke subset (10 rows)
  recall@5=0.1000 / recall@10=0.2000 / nDCG@5=0.0631 / nDCG@10=0.0946 /
  MRR=0.0625 / p50=12409.2ms / p95=14214.4ms.

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
| `src/rugbrain/eval/baselines.smoke.json` | committed thresholds (10-query subset) |
| `src/rugbrain/eval/baselines.full.json` | committed thresholds (full set) |
| `src/rugbrain/eval/baseline_select.py` | maps query set → baseline file |
| `src/rugbrain/eval/golden/crypto_rag_golden.jsonl` | golden set (41 verified rows) |
| `scripts/run-eval-smoke.sh` | fast CI subset runner |
| `scripts/run-eval-nightly.sh` | full-set nightly runner + history record |
| `eval/history.jsonl` | append-only nightly metric history |
| `tests/test_eval_harness.py` | metric math, gate semantics, schema tests |
| `requirements-eval.txt` | optional LLM-judge extras |

---

## 9. Nightly schedule (systemd timer)

The full-set nightly runner is installed as a systemd timer on the production host:

| Unit | Role |
|---|---|
| `rmi-rag-eval-nightly.timer` | fires daily at **03:30 host-local**, `Persistent=true` (catches up a missed window after reboot) |
| `rmi-rag-eval-nightly.service` | oneshot, runs `scripts/run-eval-nightly.sh` in `/srv/work/repos/rugbrain` |

Source of truth: `deploy/systemd/rmi-rag-eval-nightly.{service,timer}`. Install / update on the host:

```bash
sudo cp deploy/systemd/rmi-rag-eval-nightly.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rmi-rag-eval-nightly.timer
```

The service runs as root with `EnvironmentFile=-/srv/secrets/rugbrain-tg.env`
(supplies `QDRANT_URL` + embed env) and a 30-minute start timeout. It is
**fail-open**: when Qdrant is unreachable the script logs `eval-nightly: SKIP`
and exits 0. Set `EVAL_STRICT=1` in the env file to turn a skip into a failure.
A real gate regression exits non-zero and marks the unit `failed`.

### Reading history

Every run appends one JSON line to `eval/history.jsonl` (gitignored runtime
data) — `ts`, `git_sha`, `set`, `status`, all metrics, and the gate verdict:

```bash
tail -1 eval/history.jsonl | jq '{ts, git_sha, status, metrics, gate}'
journalctl -u rmi-rag-eval-nightly --since "24 hours ago"
```

`status` is `ok` (a real run) or `unavailable` (a skip). Trend `recall@10` /
`ndcg@10` across lines to catch silent retrieval drift.

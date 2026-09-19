#!/usr/bin/env bash
# run-eval-nightly.sh — full-set retrieval-quality gate + history record.
#
# Runs the ENTIRE golden set through the offline harness, gates it against
# eval/baselines.full.json, appends a JSON record to eval/history.jsonl, and
# exits non-zero on gate failure.
#
# Fail-open: when Qdrant is unreachable the harness reports status=unavailable
# and this script exits 0 with a SKIP notice (infrastructure absence is not a
# quality regression). Set EVAL_STRICT=1 to turn a skip into a failure.
#
# Usage:
#   scripts/run-eval-nightly.sh
#   EVAL_STRICT=1 scripts/run-eval-nightly.sh
#
# Env:
#   QDRANT_URL     Qdrant base URL (default http://127.0.0.1:6334)
#   EVAL_TOP_K     results per query (default 10)
#   EVAL_STRICT    "1" => a skip (backend down) fails the gate
#   EVAL_HISTORY   history file (default eval/history.jsonl)
#   PYTHON         python interpreter (default .venv/bin/python or python3)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

export QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6334}"
export EMBED_PROVIDER="${EMBED_PROVIDER:-local}"
export RERANK_HOSTED="${RERANK_HOSTED:-0}"

EVAL_TOP_K="${EVAL_TOP_K:-10}"
EVAL_STRICT="${EVAL_STRICT:-0}"
EVAL_HISTORY="${EVAL_HISTORY:-eval/history.jsonl}"

echo "eval-nightly: top_k=${EVAL_TOP_K} qdrant=${QDRANT_URL} history=${EVAL_HISTORY}"

# Fast pre-flight: a down Qdrant should SKIP immediately, not wait for 41
# per-query connection timeouts. Fail-open (exit 0) unless EVAL_STRICT=1.
if ! curl -sf --max-time 5 "${QDRANT_URL%/}/healthz" >/dev/null 2>&1; then
  echo "eval-nightly: SKIP — Qdrant unreachable at ${QDRANT_URL} (pre-flight)"
  if [[ "$EVAL_STRICT" == "1" ]]; then
    echo "eval-nightly: FAIL (strict) — backend unavailable"
    exit 1
  fi
  exit 0
fi

"$PYTHON" - "$EVAL_TOP_K" "$EVAL_STRICT" "$EVAL_HISTORY" <<'PY'
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from rugbrain.eval.gates import evaluate_gates, format_verdict
from rugbrain.eval.harness import direct_qdrant_retriever, run_backtest

top_k = int(sys.argv[1])
strict = sys.argv[2] == "1"
history_path = Path(sys.argv[3])


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def main() -> int:
    report = await run_backtest(
        retriever=direct_qdrant_retriever(),
        top_k=top_k,
        k_values=(5, 10),
    )
    verdict = evaluate_gates(report, limit=None)
    print(json.dumps(report.to_dict(), indent=2))
    print(format_verdict(verdict))

    record = {
        "ts": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "set": "full",
        "top_k": top_k,
        "status": report.status,
        "total": report.total,
        "errors": report.errors,
        "metrics": {
            "recall@5": round(report.mean_recall.get(5, 0.0), 4),
            "recall@10": round(report.mean_recall.get(10, 0.0), 4),
            "ndcg@5": round(report.mean_ndcg.get(5, 0.0), 4),
            "ndcg@10": round(report.mean_ndcg.get(10, 0.0), 4),
            "mrr": round(report.mean_mrr, 4),
            "p50_latency_ms": round(report.p50_latency_ms, 1),
            "p95_latency_ms": round(report.p95_latency_ms, 1),
        },
        "gate": {"passed": verdict.passed, "skipped": verdict.skipped},
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print(f"eval-nightly: recorded -> {history_path}")

    if verdict.skipped:
        if strict:
            print("eval-nightly: FAIL (strict) — report unavailable/empty")
            return 1
        print("eval-nightly: SKIP — backend unavailable, no gate applied")
        return 0
    if not verdict.passed:
        print("eval-nightly: FAIL — retrieval quality below baseline")
        return 1
    print("eval-nightly: PASS")
    return 0


raise SystemExit(asyncio.run(main()))
PY

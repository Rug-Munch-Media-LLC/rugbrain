#!/usr/bin/env bash
# run-eval-smoke.sh — fast retrieval-quality gate for CI.
#
# Runs a ~10-query subset of the golden set through the offline harness and
# fails (exit 1) when recall@10 / MRR / nDCG@10 drop below eval/baselines.json.
#
# Fail-open: when Qdrant is unreachable the harness reports status=unavailable
# and this script exits 0 with a SKIP notice (infrastructure absence is not a
# quality regression). Set EVAL_STRICT=1 to turn a skip into a failure.
#
# Usage:
#   scripts/run-eval-smoke.sh            # 10-query subset, direct-Qdrant retriever
#   EVAL_LIMIT=20 scripts/run-eval-smoke.sh
#   EVAL_STRICT=1 scripts/run-eval-smoke.sh
#
# Env:
#   QDRANT_URL     Qdrant base URL (default http://127.0.0.1:6334)
#   EVAL_LIMIT     number of golden queries to run (default 10)
#   EVAL_TOP_K     results per query (default 10)
#   EVAL_STRICT    "1" => a skip (backend down) fails the gate
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

EVAL_LIMIT="${EVAL_LIMIT:-10}"
EVAL_TOP_K="${EVAL_TOP_K:-10}"
EVAL_STRICT="${EVAL_STRICT:-0}"

echo "eval-smoke: limit=${EVAL_LIMIT} top_k=${EVAL_TOP_K} qdrant=${QDRANT_URL}"

"$PYTHON" - "$EVAL_LIMIT" "$EVAL_TOP_K" "$EVAL_STRICT" <<'PY'
import asyncio
import json
import sys

from rugbrain.eval.gates import evaluate_gates, format_verdict
from rugbrain.eval.harness import direct_qdrant_retriever, run_backtest

limit = int(sys.argv[1])
top_k = int(sys.argv[2])
strict = sys.argv[3] == "1"


async def main() -> int:
    report = await run_backtest(
        retriever=direct_qdrant_retriever(),
        top_k=top_k,
        k_values=(5, 10),
        limit=limit,
    )
    verdict = evaluate_gates(report)
    print(json.dumps(report.to_dict(), indent=2))
    print(format_verdict(verdict))

    if verdict.skipped:
        if strict:
            print("eval-smoke: FAIL (strict) — report unavailable/empty")
            return 1
        print("eval-smoke: SKIP — backend unavailable, no gate applied")
        return 0
    if not verdict.passed:
        print("eval-smoke: FAIL — retrieval quality below baseline")
        return 1
    print("eval-smoke: PASS")
    return 0


raise SystemExit(asyncio.run(main()))
PY

#!/usr/bin/env python3
"""Run the crypto-crime mission eval and gate it. Exit 1 on a regression.

This is the gate that the pre-existing nightly missed: a 100%-error run
used to exit 0, and the golden set it scored was 40/50 code-retrieval.
Here, ``unavailable`` is a FAILURE and the floors are per mission job class.

Usage::

    python scripts/run-mission-eval.py                # full set, exit 0/1
    python scripts/run-mission-eval.py --json         # machine-readable
    python scripts/run-mission-eval.py --job rug_history
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rugbrain.eval.harness import direct_qdrant_retriever
from rugbrain.missioneval.gate import evaluate_mission, format_verdict
from rugbrain.missioneval.goldens import cases_for
from rugbrain.missioneval.run import run_mission_eval_sync


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run-mission-eval")
    ap.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--job", default=None, help="restrict to one mission job class")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report-out", default=None, help="write the JSON report here")
    args = ap.parse_args(argv)

    cases = cases_for(args.job) if args.job else None
    retriever = direct_qdrant_retriever(qdrant_url=args.qdrant_url)
    report = run_mission_eval_sync(retriever, cases, top_k=args.top_k)
    verdict = evaluate_mission(report)

    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(
            json.dumps({"report": report.to_dict(), "verdict": verdict.to_dict()}, indent=2),
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps({"report": report.to_dict(), "verdict": verdict.to_dict()}, indent=2))
    else:
        print(format_verdict(verdict))
        for cls, d in sorted(report.per_class.items()):
            print(
                f"  {cls:20s} n={d['n']:<3} gold={d['gold_rate']:.3f} hit={d['hit_rate']:.3f}"
            )
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

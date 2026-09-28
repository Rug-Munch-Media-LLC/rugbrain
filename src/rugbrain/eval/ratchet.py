"""CLI for the retrieval baseline ratchet. Exit code is the contract.

    python -m rugbrain.eval.ratchet --write-baseline   # record, after review
    python -m rugbrain.eval.ratchet                    # gate
    python -m rugbrain.eval.ratchet --check-verify     # golden review worklist

Exit codes (see ``baseline_ratchet``):

    0  pass                3  baseline hash mismatch
    1  regressed           4  dimension split
    2  could-not-measure

The preflight runs before measurement, and a dimension split short-circuits
to exit 4 without reporting recall numbers, because when the serving dim and
the store width disagree every recall figure is meaningless.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rugbrain.eval import baseline_ratchet as ratchet
from rugbrain.eval import dim_preflight, ratchet_runner
from rugbrain.eval import verification as verify


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rugbrain.eval.ratchet",
        description="Retrieval baseline ratchet (hybrid production path + rerank).",
    )
    p.add_argument("--golden", type=Path, default=None, help="golden JSONL (default: shipped set)")
    p.add_argument("--baseline", type=Path, default=None, help="baseline JSON to compare against")
    p.add_argument(
        "--collection", default="codebase_reference_v2", help="collection for rows with none"
    )
    p.add_argument(
        "--limit", type=int, default=ratchet_runner.DEFAULT_LIMIT, help="candidates per query"
    )
    p.add_argument(
        "--layers", default="dense,hybrid,rerank", help="comma-separated layers to measure"
    )
    p.add_argument(
        "--no-preflight",
        action="store_true",
        help="skip the dimension-split preflight (not advised)",
    )
    p.add_argument("--skip-rerank", action="store_true", help="do not measure the rerank layer")
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="record this run as the new baseline instead of gating against the old one",
    )
    p.add_argument("--json", action="store_true", help="emit the full report as JSON")
    p.add_argument(
        "--check-verify",
        action="store_true",
        help="print the golden verification worklist and exit 0 (a review aid, not a gate)",
    )
    p.add_argument("--worklist-limit", type=int, default=20, help="rows to list for review")
    return p


def _print_verify_worklist(golden: Path, limit: int) -> int:
    rows = ratchet_runner.load_rows(golden)
    queries = [str(r.get("query", "")) for r in rows]
    evidence_path = golden.parent / verify.EVIDENCE_FILENAME
    evidence = verify.load_evidence(evidence_path)
    tally = verify.counts(queries, evidence)
    items = verify.worklist(queries, evidence, limit=limit)
    print(f"golden set      : {golden}  ({tally['total']} cases)")
    print(f"evidence sidecar: {evidence_path}  ({len(evidence)} records)")
    for key in ("verified", "unverified", "pending", "rejected"):
        print(f"  {key:<11}: {tally[key]}")
    print(f"  needs_human: {tally['needs_human']}")
    if items:
        print("\nrows awaiting a human decision:")
        for item in items:
            print(f"  {item['row_id']}  missing={','.join(item['missing'])}  {item['query'][:70]}")
    else:
        print("\nno rows awaiting review")
    return 0


async def _run(args: argparse.Namespace) -> tuple[int, dict]:
    golden = args.golden or ratchet_runner.default_golden_path()
    baseline_path = args.baseline or ratchet_runner.default_baseline_path()
    rows = ratchet_runner.load_rows(golden)
    layers = tuple(x.strip() for x in args.layers.split(",") if x.strip())
    if args.skip_rerank:
        layers = tuple(x for x in layers if x != "rerank")

    dim_checks: list[dict] = []
    preflight_split: list[dict] = []
    if not args.no_preflight:
        targets = dim_preflight.collections_from_env(
            ratchet_runner.measured_collections(rows, args.collection)
        )
        try:
            checks = await dim_preflight.preflight(targets, ratchet_runner.qdrant_url())
            dim_checks = [c.as_dict() for c in checks]
            preflight_split = dim_preflight.split_collections(checks)
        except Exception as exc:  # noqa: BLE001
            # An embedder that cannot be reached makes measurement impossible;
            # treat it as a dimension-split (the measurement is invalid), not a pass.
            return ratchet.EXIT_DIM_SPLIT, {
                "error": f"preflight failed: {exc}",
                "collections": targets,
            }

    # Short-circuit BEFORE measuring. A split means every recall number below
    # is computed against a refused or mis-scored query, so reporting a
    # regression verdict from it would attribute a dimension bug to the corpus.
    # Report the split, and the collections that would have been measured.
    if preflight_split:
        return ratchet.EXIT_DIM_SPLIT, {
            "error": "dimension preflight found a serving/store split",
            "dim_checks": dim_checks,
            "collections": targets,
        }

    measurement = await ratchet_runner.measure(
        rows,
        layers=layers,  # type: ignore[arg-type]
        limit=args.limit,
        dim_checks=dim_checks,
        default_collection=args.collection,
    )

    if args.write_baseline:
        ratchet.write_baseline(baseline_path, measurement)
        return ratchet.EXIT_PASS, {
            "written": str(baseline_path),
            "measurement": measurement.as_dict(),
        }

    baseline = ratchet.load_baseline(baseline_path)
    verdict = ratchet.evaluate(measurement, baseline)
    report = {"measurement": measurement.as_dict(), "verdict": verdict.as_dict()}
    return verdict.exit_code, report


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    golden = args.golden or ratchet_runner.default_golden_path()
    if args.check_verify:
        return _print_verify_worklist(golden, args.worklist_limit)

    code, report = asyncio.run(_run(args))

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return code

    meaning = ratchet.EXIT_MEANING.get(code, "unknown")
    print(f"exit {code} ({meaning})")
    if "error" in report:
        print(f"  {report['error']}")
        if report.get("collections"):
            print(f"  collections measured: {len(report['collections'])}")
        for check in report.get("dim_checks", []):
            if not check.get("ok", True):
                print(f"    {check['collection']}: {check['detail']}")
        return code
    if "written" in report:
        m = report["measurement"]
        print(f"  baseline written: {report['written']}  corpus={m['corpus_hash'][:12]}")
        for name, metrics in sorted(m["layers"].items()):
            print(
                f"    {name:<7} r@5={metrics['recall_at_5']:.3f} r@10={metrics['recall_at_10']:.3f} "
                f"mrr={metrics['mrr']:.3f} ndcg@10={metrics['ndcg_at_10']:.3f} "
                f"(measured={metrics['measured']} errors={metrics['errors']})"
            )
        return code

    m = report["measurement"]
    v = report["verdict"]
    print(f"  corpus: {m['corpus_hash'][:12]}  cases={m['case_count']}")
    for name, metrics in sorted(m["layers"].items()):
        print(
            f"    {name:<7} r@5={metrics['recall_at_5']:.3f} r@10={metrics['recall_at_10']:.3f} "
            f"mrr={metrics['mrr']:.3f} ndcg@10={metrics['ndcg_at_10']:.3f} "
            f"(measured={metrics['measured']} errors={metrics['errors']})"
        )
    print(f"  {v['reason']}")
    for reg in v.get("regressions", []):
        print(
            f"    REGRESSION [{reg['layer']}] {reg['metric']}: "
            f"{reg['baseline']} -> {reg['current']} (-{reg['delta']})"
        )
    return code


if __name__ == "__main__":
    sys.exit(main())

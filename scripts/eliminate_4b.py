#!/usr/bin/env python3
"""Fill the _8b twins for the lagging collections so 2560 can be deleted.

The 12 target collections are planned by point-id SET DIFFERENCE, not by
count arithmetic, because ``codebase_reference_v2_8b`` already holds
2,126,630 of 2,131,274 — a wholesale re-embed would spend ~2.1M calls to
place 4,644 vectors that are already there.

DRY RUN BY DEFAULT. Nothing is written without an explicit --execute.

USAGE
    python3 scripts/eliminate_4b.py                       # dry run, full report
    python3 scripts/eliminate_4b.py --only scan_intel
    python3 scripts/eliminate_4b.py --execute --only research_v2 --limit 200
    python3 scripts/eliminate_4b.py --verify-only

The implementation is in the ``eliminate_4b`` package beside this file.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from eliminate_4b import qdrant_io as q  # noqa: E402
from eliminate_4b.embed import deepinfra_key  # noqa: E402
from eliminate_4b.fill import fill  # noqa: E402
from eliminate_4b.plan import TARGETS, Target, plan  # noqa: E402
from eliminate_4b.report import print_eta, print_table  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eliminate_4b")

TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def select(only: list[str]) -> list[Target]:
    if not only:
        return list(TARGETS)
    known = {t.base for t in TARGETS}
    unknown = sorted(set(only) - known)
    if unknown:
        raise SystemExit(f"unknown collection(s): {unknown}")
    return [t for t in TARGETS if t.base in only]


def run_execute(c: httpx.Client, targets: list[Target], limit: int | None) -> int:
    key = deepinfra_key()
    if not key:
        print("no DeepInfra key; refusing to start a partial run", file=sys.stderr)
        return 2
    rows = []
    t0 = time.monotonic()
    with httpx.Client(timeout=TIMEOUT) as h:
        for t in targets:
            rep = plan(c, t)
            if rep.status != "OK":
                rows.append(rep)
                continue
            if rep.to_embed == 0:
                rep.status, rep.twin_after = "IN-PARITY", rep.twin_before
                rows.append(rep)
                continue
            logger.info("embedding %s: %d points", rep.base, rep.to_embed)
            fill(c, h, rep, key, limit)
            # VERIFY: parity or FAIL, never move on past a shortfall.
            rep.status = "OK" if rep.parity else "FAILED"
            if not rep.parity:
                rep.failed.append(f"twin {rep.twin_after} < base {rep.base_count}")
            rows.append(rep)
    elapsed = max(time.monotonic() - t0, 1e-9)
    print_table(rows, executed=True)
    print_eta(rows, sum(r.embedded for r in rows) / elapsed)
    return 1 if any(r.status == "FAILED" for r in rows) else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--execute", action="store_true", help="actually write. Default is a dry run.")
    ap.add_argument(
        "--only", action="append", default=[], help="restrict to these collections (repeatable)"
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="cap points embedded per collection (smoke test)"
    )
    ap.add_argument(
        "--verify-only", action="store_true", help="re-measure parity and write nothing"
    )
    args = ap.parse_args()

    targets = select(args.only)
    with httpx.Client(timeout=TIMEOUT) as c:
        rows = [plan(c, t) for t in targets]
        print_table(rows, executed=False)
        if not args.execute:
            print("\nDRY RUN — nothing written. Add --execute to embed.")
            return 0
        # Refuse the whole run if any collection breaks the dim/twin contract,
        # rather than embedding the easy 8 and quietly skipping the hard one.
        refused = [r.base for r in rows if r.status == "REFUSED"]
        if refused:
            print(
                f"\nREFUSING to run: {', '.join(refused)} failed the dim/twin "
                f"contract. Fix them, or exclude with --only. No writes done."
            )
            return 1
        if args.verify_only:
            return 0
        return run_execute(c, targets, args.limit)


if __name__ == "__main__":
    sys.exit(main())

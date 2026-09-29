#!/usr/bin/env python3
"""CLI for the dimension-drift gate. Exit 0 clean, non-zero on drift.

    python3 scripts/check_dim_drift.py              # both halves
    python3 scripts/check_dim_drift.py --live       # Qdrant only
    python3 scripts/check_dim_drift.py --static     # source tree only
    python3 scripts/check_dim_drift.py --json       # machine-readable

READ-ONLY BY CONSTRUCTION. Qdrant :6334 is production: this script issues GET
and nothing else, and the module it calls (:mod:`rugbrain.dim_drift`) contains
no write, create or delete path.

Exit codes
    0  both halves clean
    1  drift found (a non-canonical collection, or an executable reference to
       the retired generation)
    2  the live half could not reach Qdrant — UNKNOWN, not clean. Distinguished
       from 1 because "cannot tell" must never read as "fine".

RED RIGHT NOW IS CORRECT. 42 collections are still at 2560 and 6 at 1024 while
the bases are pending deletion. This gate is not weakened to go green; the
offenders are named so the elimination can be driven by the list.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from rugbrain.dim_drift import (  # noqa: E402
    CANONICAL_DIM,
    canonical_model,
    retired_dims,
    retired_models,
    scan_live,
    scan_static,
)

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNREACHABLE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when the retired embedding generation reappears (live + static).",
    )
    parser.add_argument("--live", action="store_true", help="Qdrant census only")
    parser.add_argument("--static", action="store_true", help="source-tree scan only")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--qdrant-url", default=None, help="override QDRANT_URL")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="PATH",
        help="path to exclude from the static scan (repeatable)",
    )
    args = parser.parse_args(argv)

    both = not args.live and not args.static
    run_live = both or args.live
    run_static = both or args.static

    live = scan_live(args.qdrant_url) if run_live else None
    static = scan_static(skip=args.skip) if run_static else None

    if args.json:
        payload: dict[str, object] = {
            # Top level, not nested under a half: a machine consumer asking
            # "what width is canonical" should not have to know which half ran.
            "canonical_model": canonical_model(),
            "canonical_dim": CANONICAL_DIM,
            "retired_dims": sorted(retired_dims()),
            "retired_models": dict(retired_models()),
        }
        ok = True
        if live is not None:
            payload["live"] = live.to_dict()
            ok = ok and live.ok
        if static is not None:
            payload["static"] = static.to_dict()
            ok = ok and static.ok
        payload["ok"] = ok
        print(json.dumps(payload, indent=2))
    else:
        if live is not None:
            print(f"LIVE      canonical={live.canonical_dim} checked={live.checked}")
            if not live.reachable:
                print(f"          UNREACHABLE {live.error}")
            for name, dims, reason in live.offenders:
                print(f"          FAIL {name}: dims={','.join(map(str, dims))} — {reason}")
            for name, reason in live.unparseable:
                print(f"          UNPARSED {name}: {reason}")
            if live.ok and live.reachable:
                print(f"          PASS all {live.checked} collections at {live.canonical_dim}")
        if static is not None:
            print(f"STATIC    scanned={static.scanned}")
            for path, line, cls, detail in static.failures:
                print(f"          FAIL {path}:{line} [{cls}] {detail}")
            for path, line, cls, detail in static.declarations:
                print(f"          ok   {path}:{line} [{cls}] {detail}")
            for path, line, _cls, detail, is_doc in static.prose:
                kind = "docstring" if is_doc else "comment"
                print(f"          prose {path}:{line} [{kind}] {detail}")
            for path, reason in static.unparsed:
                print(f"          UNPARSED {path}: {reason}")
            if static.ok:
                print(
                    f"          PASS no executable reference to the retired generation "
                    f"({len(static.declarations)} classified as declaration/record)"
                )

    if live is not None and not live.reachable:
        return EXIT_UNREACHABLE
    ok = (live is None or live.ok) and (static is None or static.ok)
    return EXIT_OK if ok else EXIT_DRIFT


if __name__ == "__main__":
    raise SystemExit(main())

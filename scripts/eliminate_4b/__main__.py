"""CLI for the 2560 -> 4096 twin fill. DRY RUN UNLESS --execute.

    python -m scripts.eliminate_4b                 # measure, write nothing
    python -m scripts.eliminate_4b --execute       # fill, verify, delete nothing
    python -m scripts.eliminate_4b --only rug_events --limit 5 --execute

--execute is the only path that writes to production Qdrant, and it can only
CREATE a missing twin (empty, for the three no-twin collections) or upsert
into one. There is no delete anywhere in this package.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx

from . import qdrant_io as q
from .embed import DEEPINFRA_KEY_FILE, deepinfra_key
from .fill import fill
from .plan import plan
from .report import print_excluded, print_table, print_verdict
from .targets import EXCLUDED_BASES, TARGETS

logger = logging.getLogger("eliminate_4b")


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(120.0, read=300.0))


def _excluded_counts(c: httpx.Client) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in sorted(EXCLUDED_BASES):
        info = q.collection_info(c, name)
        out[name] = int(info["points_count"]) if info else 0
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eliminate_4b", description=__doc__)
    ap.add_argument("--execute", action="store_true", help="write to Qdrant (default: dry run)")
    ap.add_argument("--only", action="append", default=[], help="restrict to a base collection")
    ap.add_argument("--limit", type=int, default=None, help="stop after N points per collection")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    targets = [t for t in TARGETS if not args.only or t.base in args.only]
    if not targets:
        print(f"no target matches {args.only}", file=sys.stderr)
        return 2

    c, h = _client(), httpx.Client(timeout=httpx.Timeout(300.0))
    key = deepinfra_key() if args.execute else ""
    if args.execute and not key:
        print(
            f"refusing to execute: no DeepInfra key (env DEEPINFRA_API_KEY or {DEEPINFRA_KEY_FILE})",
            file=sys.stderr,
        )
        return 2

    rows = []
    started = time.monotonic()
    excluded = _excluded_counts(c)
    try:
        for t in targets:
            rep = plan(c, t, execute=args.execute)
            if args.execute and rep.status in ("OK", "CREATE-ON-EXECUTE") and rep.to_embed:
                fill(c, h, rep, key, limit=args.limit)
            elif args.execute and rep.created_twin:
                # Created but nothing to embed (already-parity twin): still
                # verify what exists rather than trusting the count.
                rep.twin_after = q.count(c, rep.twin)
            rows.append(rep)
    finally:
        c.close()
        h.close()

    print()
    print_table(rows, executed=args.execute)
    print_excluded(excluded)

    ok = print_verdict(rows, executed=args.execute)

    embedded = sum(r.embedded for r in rows)
    if args.execute and embedded:
        el = time.monotonic() - started
        print(f"\n{embedded} points embedded in {el:.1f}s ({embedded / el:.1f} pt/s).")
        print("Re-run after a crash embeds only the still-missing tail (diff is by id).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

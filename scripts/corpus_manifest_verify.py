"""The ``--verify`` half of ``corpus_manifest.py``: probe live Qdrant, GET only.

Separated from the report half so each stays under the repo's 200-line cap, and so
the read-only path can be read on its own. It is the only place in the script that
touches the network and it touches it with GET — no PUT, PATCH or DELETE, matching
the coverage audit's method (``docs/COVERAGE-AUDIT-2026-09-28.md``).

EXIT CODES ARE DISTINCT ON PURPOSE
    0 no findings · 1 findings (undeclared collections, or a dim mismatch)
    2 could not verify. A Qdrant that is down must never read as "no dim problems
    found" — that is how a fleet runs for a month on a stale belief.
"""

from __future__ import annotations

import sys


async def run_verify() -> int:
    """Probe every live collection. Returns the process exit code."""
    import httpx

    from rugbrain.corpus import verify_live_widths
    from rugbrain.qdrant_store.connection import QDRANT_URL

    url = QDRANT_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=url) as client:
            report = await verify_live_widths(client)
    except Exception as exc:
        print(f"COULD NOT VERIFY against {url}: {exc}", file=sys.stderr)
        return 2

    print(f"LIVE VERIFICATION (GET only) against {url}\n")
    print(f"  {report.summary()}\n")

    if report.undeclared_collections:
        n = len(report.undeclared_collections)
        print(f"  UNDECLARED — live in Qdrant, absent from the manifest ({n}):")
        for name in report.undeclared_collections:
            print(f"    {name}")
        print("    -> each is a corpus that could be embedded without being categorised.\n")

    if report.mismatches:
        n = len(report.mismatches)
        print(f"  DIM MISMATCH — declared dim disagrees with live width ({n}):")
        for m in report.mismatches:
            print(f"    {m.collection:<34} declared={m.declared_dim} live={m.live_dim}  {m.detail}")
        print()

    if report.declared_but_absent:
        n = len(report.declared_but_absent)
        print(f"  DECLARED BUT ABSENT from Qdrant ({n}):")
        for name in report.declared_but_absent:
            print(f"    {name}")
        print("    -> declared targets whose collection has not been created yet.\n")

    if report.unreachable:
        n = len(report.unreachable)
        print(f"  UNREACHABLE / AMBIGUOUS — a measurement gap, not a finding ({n}):")
        for name in report.unreachable:
            print(f"    {name}")
        print()

    return 1 if (report.undeclared_collections or report.mismatches) else 0


__all__ = ["run_verify"]

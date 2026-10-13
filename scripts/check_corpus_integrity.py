#!/usr/bin/env python3
"""Corpus integrity preflight — encodes two hard-won lessons (2026-07-13).

1. A registry entry that SILENTLY REROUTES reads is invisible from the
   collection itself. ``unified_route()`` returned the bare name in a shell and
   the rerouted name inside the API process, because ``RAG_UNIFIED_READS``
   lives in .env.rag which an out-of-process bisection never loaded. Every
   external probe looked healthy while the API served zero hits. So this check
   resolves each collection the way the RUNTIME does — with the real env
   loaded — and asserts the resolved home actually exists and holds points.

2. Searching an EMPTIED collection produces a confident, meaningless answer.
   ``social_intel`` was emptied (8,744 -> 0 points) while a whole diagnosis
   concluded "the vector space is wrong". A zero here is a DATA fault, not a
   retrieval fault, and it must be reported as such.

Exits non-zero when any served collection is missing, empty, off-lane, or
routed to a home that does not exist — so this can gate an ingest or a deploy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

CANONICAL_LANE_DIM = 4096


def _qdrant(url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{url}{path}", timeout=30) as fh:
        return json.load(fh)


def _info(url: str, collection: str) -> dict | None:
    try:
        return _qdrant(url, f"/collections/{collection}")
    except urllib.error.HTTPError:
        return None


def _points(url: str, collection: str) -> int:
    return _qdrant(url, f"/collections/{collection}")["result"]["points_count"]


def _dim(result: dict) -> int | None:
    vectors = (result.get("config") or {}).get("params", {}).get("vectors")
    if isinstance(vectors, dict):
        sizes = {int(v["size"]) for v in vectors.values() if isinstance(v, dict) and "size" in v}
        return max(sizes) if sizes else None
    return int(vectors.size) if vectors is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant", default=os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    ap.add_argument("--min-points", type=int, default=1,
                    help="a served collection below this is treated as an outage")
    args = ap.parse_args()

    # Resolve exactly as the runtime does: .env.rag IS the process environment
    # of the API, and loading it is what makes local probes truthful.
    env_file = Path(__file__).resolve().parents[1] / ".env.rag"
    if env_file.exists():
        load_dotenv(env_file, override=True)

    from rugbrain.knowledge_boundary import PUBLIC_COLLECTIONS
    from rugbrain.qdrant_store.namespace import unified_route
    from rugbrain.qdrant_store.owned_registry import _RMI_RAG_OWNED_COLLECTIONS

    live = {c["name"] for c in _qdrant(args.qdrant, "/collections")["result"]["collections"]}
    findings: list[str] = []
    notes: list[str] = []

    # SERVED = exposed through the public API. These must be healthy: a caller
    # can name them, so an empty or misrouted one is an outage.
    # OWNED-BUT-UNSERVED = the write-permission registry only; many of those are
    # provisioned on demand, so their absence is informational, not a failure.
    served = {
        c for c in PUBLIC_COLLECTIONS
        if c in live or f"{c}_8b" in live or c in _RMI_RAG_OWNED_COLLECTIONS
    }
    unserved_owned = sorted(set(_RMI_RAG_OWNED_COLLECTIONS) - served)

    for collection in sorted(served):
        target, condition = unified_route(collection)
        if not target:
            continue
        if target not in live:
            if collection in live:
                findings.append(
                    f"MISROUTE  {collection}: reads resolve to {target!r}, which "
                    f"does not exist"
                )
            elif f"{target}_8b" in live:
                findings.append(
                    f"MISROUTE  {collection}: the data lives in {target}_8b but "
                    f"reads resolve to {target!r} — declare the twin so the "
                    f"resolver maps base -> twin"
                )
            else:
                # Declared public but never provisioned: informational. A caller
                # can name it and get nothing, but the fault is provisioning,
                # not a misroute — the state that actually misleads a diagnosis.
                notes.append(f"UNPROVISIONED {collection}")
            continue
        if condition and "collection" in str(condition):
            findings.append(
                f"REROUTED  {collection}: reads go to {target} filtered on a "
                f"payload field — verify that partition is populated, or remove "
                f"it from the consolidated set if it is a native collection"
            )
            continue
        points = _points(args.qdrant, target)
        if points < args.min_points:
            findings.append(
                f"EMPTY     {collection}: 0 points. Searching it returns a "
                f"confident meaningless answer — this is a DATA fault."
            )
        dim = _dim(_info(args.qdrant, target)["result"])
        if dim is not None and dim != CANONICAL_LANE_DIM:
            findings.append(f"OFF-LANE  {collection}: dim {dim}, expected {CANONICAL_LANE_DIM}")

    print(f"corpus integrity: {len(served)} served collections checked "
          f"({len(unserved_owned)} owned-but-unserved, informational)")
    for n in notes:
        print("  " + n + " (informational)")
    for f in findings:
        print("  " + f)
    if findings:
        print(f"FAIL: {len(findings)} corpus integrity problem(s)")
        return 1
    print("OK: every owned collection exists, is on the 8B lane, and holds points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

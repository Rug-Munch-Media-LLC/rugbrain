#!/usr/bin/env python3
"""Report the corpus manifest: declarations, undeclared collections, dim mismatches.

READ-ONLY. This script issues GET requests only — no PUT, PATCH or DELETE — and
exists so an operator can see the manifest without writing code. It is NOT the
source of truth: it imports ``rugbrain.corpus`` and prints it. A second answer
computed here would be the exact failure mode this package exists to remove.

Usage
    python scripts/corpus_manifest.py                    # declarations from the manifest
    python scripts/corpus_manifest.py --verify           # also probe live Qdrant (GET)
    python scripts/corpus_manifest.py --verify --json    # machine-readable
    python scripts/corpus_manifest.py --licence          # what is cleared for a product
    python scripts/corpus_manifest.py --unverified       # the 67%-blind freshness finding

Exit codes
    0  no findings (or no --verify)
    1  findings: undeclared collections, dim mismatches, or stale declarations
    2  could not verify (Qdrant unreachable) — distinct from "clean"

The licence ruling in force: the corpus is INTERNAL and PRIVATE and nothing is
sold, so the test is REDISTRIBUTION, not use. No-licence, CC-BY-NC and
gated-but-token-accessible corpora are all USABLE and are reported as usable, with
their declared licence string retained so a product-bound day can identify the
ones that were never cleared for that.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.corpus import DECLARED, verify_live_widths
from rugbrain.corpus.schema import LicenceStatus
from rugbrain.qdrant_store.connection import QDRANT_URL

#: Beyond this age a verification is stale. The audit's own cadence is weekly.
STALE_AFTER_DAYS = 7


def _print_declarations() -> None:
    print(f"CORPUS MANIFEST — {len(DECLARED)} declarations\n")
    print(f"{'collection':<34} {'domain':<13} {'language':<11} {'dim':>5} {'live':>5}  cadence")
    print("-" * 100)
    for d in sorted(DECLARED, key=lambda x: (str(x.domain), x.collection)):
        live = "-" if d.observed_live_dim is None else str(d.observed_live_dim)
        print(
            f"{d.collection:<34} {d.domain!s:<13} {d.language!s:<11} {d.dim:>5} {live:>5}  {d.refresh_cadence}"
        )
    print()
    for axis, label in (("domain", "DOMAIN"), ("language", "LANGUAGE")):
        counts = Counter(str(getattr(d, axis)) for d in DECLARED)
        print(f"{label}: " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    print()


def _print_licences() -> None:
    print("LICENCE — usable internally under the internal/private ruling.\n")
    by_status: dict[LicenceStatus, list] = {}
    for d in DECLARED:
        by_status.setdefault(d.licence_status, []).append(d)
    for status, items in sorted(by_status.items(), key=lambda kv: str(kv[0])):
        print(f"  {status} ({len(items)}):")
        for d in sorted(items, key=lambda x: x.collection):
            mark = "OK " if d.declared_for_redistribution() else "-- "
            print(f"    {mark}{d.collection:<32} licence={d.licence[:56]}")
    print(
        "\n  OK = cleared for a shipped product; -- = usable internally, never product-cleared.\n"
    )


def _print_unverified() -> None:
    blind = [d for d in DECLARED if d.last_verified is None]
    print(
        f"UNVERIFIED — {len(blind)} of {len(DECLARED)} declarations have no last-verified date.\n"
    )
    for d in sorted(blind, key=lambda x: x.collection):
        print(f"  {d.collection:<34} cadence={d.refresh_cadence}")
    print(
        "\n  Audit §5.2: 48 of 61 collections carry no ingest-side timestamp, so for\n"
        "  4.26M points (67% of the fleet) nobody can say when the data was last written.\n"
    )


async def _verify_json() -> dict[str, object]:
    """The same verification, as a dict. Shares the probe with the text report."""
    import httpx

    from rugbrain.corpus import verify_live_widths

    async with httpx.AsyncClient(base_url=QDRANT_URL.rstrip("/")) as client:
        r = await verify_live_widths(client)
    return {
        "checked": r.checked,
        "matched": r.matched,
        "undeclared_collections": r.undeclared_collections,
        "mismatches": [
            {"collection": m.collection, "declared": m.declared_dim, "live": m.live_dim}
            for m in r.mismatches
        ],
        "declared_but_absent": r.declared_but_absent,
        "unreachable": r.unreachable,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--verify", action="store_true", help="probe live Qdrant (GET only)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--licence", action="store_true", help="licence breakdown")
    ap.add_argument(
        "--unverified", action="store_true", help="declarations with no last-verified date"
    )
    args = ap.parse_args()

    if args.json:
        payload: dict[str, object] = {
            "measured_on": DECLARED[0].last_verified,
            "count": len(DECLARED),
            "declarations": [
                {
                    "id": d.id,
                    "collection": d.collection,
                    "language": str(d.language),
                    "domain": str(d.domain),
                    "declared_dim": d.dim,
                    "observed_live_dim": d.observed_live_dim,
                    "licence": d.licence,
                    "licence_status": str(d.licence_status),
                    "product_cleared": d.declared_for_redistribution(),
                    "source_url": d.source_url,
                    "refresh_cadence": d.refresh_cadence,
                    "last_verified": d.last_verified,
                }
                for d in DECLARED
            ],
        }
        if args.verify:
            import httpx

            async def run() -> dict[str, object]:
                async with httpx.AsyncClient(base_url=QDRANT_URL.rstrip("/")) as c:
                    r = await verify_live_widths(c)
                    return {
                        "checked": r.checked,
                        "matched": r.matched,
                        "undeclared_collections": r.undeclared_collections,
                        "mismatches": [
                            {
                                "collection": m.collection,
                                "declared": m.declared_dim,
                                "live": m.live_dim,
                            }
                            for m in r.mismatches
                        ],
                        "declared_but_absent": r.declared_but_absent,
                        "unreachable": r.unreachable,
                    }

            payload["verification"] = asyncio.run(run())
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if args.licence:
        _print_licences()
    elif args.unverified:
        _print_unverified()
    else:
        _print_declarations()

    if args.verify:
        from corpus_manifest_verify import run_verify

        return asyncio.run(run_verify())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

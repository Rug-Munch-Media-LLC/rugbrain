"""Fail when a crypto collection\'s newest ingest is older than THRESHOLD_DAYS.

READ-ONLY against Qdrant. Scrolls payloads for the newest ingest-side timestamp
per collection. It never writes, upserts or deletes a point and never triggers
a re-ingest: refreshing a stale feed would destroy the evidence this check
exists to report. Exit 0 fresh / 1 stale / 2 config or runtime error.

CONTENT DATES ARE NOT INGEST DATES
----------------------------------
Content dates ("when the exploit happened", `metadata.due_date`) are properties
of the world, not of the corpus. Measured: `rag_documents` and `defi_exploits`
both carry content dates of 2026-12-08 - eleven days in the FUTURE - which read
as "fresh". Only a field the ingester stamps on arrival answers "when was this
collection last fed".

MEASUREMENT AND BLINDNESS ARE SEPARATE ANSWERS
-----------------------------------------------
This check used to report 61 collections and could not measure 48, which is
where the blindness came from: an unmeasurable collection was a silent omission,
not a finding. Measurement now lives in `rugbrain.collection_freshness`, which
returns exactly one of two outcomes per collection:

  MEASURED      an ingest-side field was found; the age is real and is judged.
  UNMEASURABLE  no such field, the collection is empty, or it is served from
                another store. Carries the reason.

A QDRANT GAP IS NOT A FLEET GAP. The wallet-label corpora are declared
`internal://neo4j/wallet-labels`; a Qdrant-side absence for one of those is
reported `off_qdrant` with its declared home, never as stale. Calling a
well-fed Neo4j corpus stale is a false alarm that teaches people to ignore this.

Gate policy is unchanged and deliberate: exit 1 on MEASURED staleness only. A
permanently-red gate gets muted, and a muted gate is worse than none - so the
unmeasurable set is printed in full every run and named in
docs/FRESHNESS-UNBLIND-2026-09-28.md rather than left as a gap nobody owns.

THRESHOLD = 5 days, from the measured age distribution (2026-09-27)
---------------------------------------------------------------------
The collections carrying an ingest timestamp split cleanly in two: alive
(0-4d) and stale (17d+), with an EMPTY gap between. sqrt(2 * 16) = 5.66, floored
to 5: inside that gap with margin on BOTH sides, 2.5x above the alive cluster
(so a weekend outage cannot false-alarm) and 3.2x below the first stale
collection (a dead feed alerts in 5 days, not 17).

One threshold, not per-feed: ingestion runs at 15 min (telegram), 6 h
(threat-intel) and 24 h (retention). A 5-day budget is ~480 missed cycles of the
fastest lane, so one number covers every cadence without per-feed tuning that
could re-baseline a dead feed into looking alive.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.collection_freshness import (
    DEFAULT_QDRANT_URL,
    FreshnessVerdict,
    Measurement,
    build_report,
    list_collections,
)
from rugbrain.freshness_transport import QdrantError

THRESHOLD_DAYS = 5

# Owned by a different change: the `_8b` duplicates and the `*_test` scratch
# collections are NOT checked here, and this script must never touch them.
EXCLUDED_SUFFIXES = ("_8b", "_test", "_tailtest")
EXCLUDED_NAMES = frozenset({"sp_tailtest", "security_patterns_test"})


def included(name: str) -> bool:
    """True when this collection is in scope for the crypto staleness gate.

    The `_8b` twins and the scratch collections are a different change's
    business. This script must not touch them.
    """
    return name not in EXCLUDED_NAMES and not name.endswith(EXCLUDED_SUFFIXES)


def main(argv: list[str] | None = None) -> int:
    """Run the check. Returns 0 fresh, 1 measured-stale, 2 config error."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threshold-days", type=int, default=THRESHOLD_DAYS)
    ap.add_argument("--sample-cap", type=int, default=250)
    ap.add_argument("--url", default=DEFAULT_QDRANT_URL)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    try:
        names = [n for n in list_collections(args.url) if included(n)]
    except QdrantError as exc:
        print(f"STALENESS-CHECK ERROR: {exc}", file=sys.stderr)
        return 2

    probes = build_report(args.url, cap=args.sample_cap, only=names)
    measured = [p for p in probes if p.measurement is Measurement.MEASURED]
    stale = [
        p
        for p in measured
        if p.verdict is FreshnessVerdict.STALE or (p.age_days or 0) > args.threshold_days
    ]
    blind = [p for p in probes if p.measurement is Measurement.UNMEASURABLE]

    if args.as_json:
        print(
            json.dumps(
                {
                    "threshold_days": args.threshold_days,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "rows": [p.as_dict() for p in sorted(probes, key=lambda r: r.collection)],
                },
                indent=1,
            )
        )
        return 1 if stale else 0

    for probe in sorted(stale, key=lambda p: -(p.age_days or 0)):
        print(
            f"STALE  {probe.collection:<26} newest_ingest={probe.newest.isoformat()}"
            f" ({probe.field}) age={probe.age_days}d  threshold={args.threshold_days}d"
        )
    for probe in sorted(measured, key=lambda p: p.collection):
        if probe in stale:
            continue
        print(f"OK     {probe.collection:<26} age={probe.age_days}d  field={probe.field}")

    off_qdrant = [p for p in blind if p.reason == "off_qdrant"]
    no_field = [p for p in blind if p.reason == "no_ingest_field"]
    empty = [p for p in blind if p.reason == "empty"]
    errors = [p for p in blind if p.reason.startswith("probe_error")]

    print(f"\n--- UNMEASURABLE ({len(blind)}) — a collection that cannot state its own age ---")
    for probe in sorted(no_field, key=lambda p: -p.points):
        print(
            f"BLIND  {probe.collection:<26} no ingest field in {probe.scanned} scanned"
            f" · {probe.points} pts"
        )
    for probe in sorted(off_qdrant, key=lambda p: p.collection):
        print(
            f"OTHER  {probe.collection:<26} served from"
            f" {probe.store.value if probe.store else '?'} (declared home) — not a Qdrant gap"
        )
    for probe in sorted(empty, key=lambda p: p.collection):
        print(f"EMPTY  {probe.collection:<26} declared, never fed · {probe.points} pts")
    for probe in errors:
        print(f"ERROR  {probe.collection:<26} {probe.reason}")

    blind_points = sum(p.points for p in blind)
    total_points = sum(p.points for p in probes)
    share = (100.0 * blind_points / total_points) if total_points else 0.0
    print(
        f"\n{len(probes)} collection(s) checked · {len(measured)} measured · {len(stale)} stale"
        f" · {len(blind)} unmeasurable · blind mass {blind_points}/{total_points} pts"
        f" ({share:.1f}%) · threshold={args.threshold_days}d"
    )
    if blind:
        print(
            f"\nThe unmeasurable set is NOT clean. {len(blind)} collections carry no ingest"
            f" timestamp ({blind_points} blind points, {share:.1f}% of corpus mass) — see"
        )
        print("docs/FRESHNESS-UNBLIND-2026-09-28.md for the per-item reason and what it would take")
        print("to measure each one.")
    if stale:
        print("\nA collection with no recent ingest is a DEAD FEED. Check its unit/cron:")
        print("  systemctl list-timers | grep -Ei 'rag|ingest'")
        print("  journalctl -u <unit> --since '-48 hours'")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())

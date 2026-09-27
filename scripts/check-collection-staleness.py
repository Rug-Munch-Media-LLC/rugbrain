#!/usr/bin/env python3
"""Fail when a crypto collection's newest document is older than THRESHOLD_DAYS.

READ-ONLY against Qdrant. Scrolls payloads for the newest ingest-side timestamp
per collection. It never writes, upserts or deletes a point and never triggers
a re-ingest: refreshing a stale feed would destroy the evidence this check
exists to report. Exit 0 fresh / 1 stale / 2 config or runtime error.

CONTENT DATES ARE NOT INGEST DATES
----------------------------------
Content dates ("when the exploit happened", `metadata.due_date`) are
properties of the world, not of the corpus. Measured: `rag_documents` and
`defi_exploits` both carry content dates of 2026-12-08 - eleven days in the
FUTURE - which read as "fresh". Only a field the ingester stamps on arrival
answers "when was this collection last fed".

THRESHOLD = 5 days, from the measured age distribution (2026-09-27)
---------------------------------------------------------------------
The 10 collections carrying an ingest timestamp split cleanly in two:

    alive   0d 0d 0d 0d 1d 2d 2d 2d     (max 2d)
    stale  16d 27d 27d ...               (min 16d)

The sorted ages leave an EMPTY gap between 2d and 16d - nothing lands in it.
sqrt(2 * 16) = 5.66, floored to 5: inside that gap with margin on BOTH sides,
2.5x above the alive cluster (a weekend outage plus a missed run cannot
false-alarm) and 3.2x below the first stale collection (a dead feed alerts in
5 days, not 16).

One threshold, not per-feed: ingestion runs at 15 min (telegram web/userbot),
6 h (threat-intel sweep) and 24 h (retention + consolidation). A 5-day budget
is ~480 missed cycles of the fastest lane, so one number covers every cadence
without per-feed tuning that could re-baseline a dead feed into looking alive.

MISSING TIMESTAMPS ARE REPORTED, NOT FAILED
-------------------------------------------
30 of 40 crypto collections carry no ingest-side field at all. That is a real
instrumentation gap, but a permanently-red gate gets muted and a muted gate is
worse than none - so the check fails on MEASURED staleness only and lists the
unverifiable set, keeping the gap visible and shrinking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Ingest-side fields, most-trusted first. Ordered deliberately: a collection
# that stamps `ingested_at` is preferred over one that only stamps `ts`.
INGEST_FIELDS = (
    "metadata.ingested_at",
    "ingested_at",
    "metadata.detected_at",
    "detected_at",
    "metadata.ts",
    "ts",
    "created_at",
    "metadata.created_at",
    "metadata.first_seen",
    "first_seen",
)

THRESHOLD_DAYS = 5
SAMPLE_CAP = 25_000  # per collection; see coverage note in main()
DEFAULT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
PAGE = 2000
TIMEOUT = 60

# Owned by a different change: the `_8b` duplicates and the `*_test` scratch
# collections are NOT checked here, and this script must never touch them.
EXCLUDED_SUFFIXES = ("_8b", "_test", "_tailtest")
EXCLUDED_NAMES = frozenset({"sp_tailtest", "security_patterns_test"})


class QdrantError(RuntimeError):
    """Qdrant was unreachable or answered something unusable."""


def qget(path: str, url: str, body: dict | None = None) -> dict:
    """GET (or POST-scroll) Qdrant. Raises QdrantError, never bare-excepts."""
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.URLError as exc:
        raise QdrantError("qdrant unreachable at %s: %s" % (path, exc.reason)) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise QdrantError("qdrant returned non-JSON for %s" % path) from exc


def leaves(node: object, prefix: str = "") -> dict:
    """Flatten a payload to dotted-path leaves. Lists are sampled, not walked."""
    if isinstance(node, dict):
        out: dict = {}
        for key, val in node.items():
            out.update(leaves(val, prefix + key + "."))
        return out
    if isinstance(node, list):
        out = {}
        for item in node[:5]:
            out.update(leaves(item, prefix))
        return out
    return {prefix.rstrip("."): node}


def parse_ts(value: object) -> datetime | None:
    """Parse a scalar into an aware UTC datetime, or None if unusable."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        secs = float(value)
        if secs > 1e12:
            secs /= 1000.0
        if secs <= 1e9:
            return None
        try:
            return datetime.fromtimestamp(secs, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or len(value) < 8:
        return None
    text = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            got = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            continue
        return got.replace(tzinfo=timezone.utc) if got.tzinfo is None else got
    return None


def iter_points(coll: str, url: str, cap: int):
    """Yield up to `cap` points by scroll. Read-only; fails closed."""
    offset, seen = None, 0
    while seen < cap:
        body = {"limit": min(PAGE, cap - seen), "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = qget("/collections/%s/points/scroll" % coll, url, body).get("result", {})
        points = result.get("points") or []
        for point in points:
            yield point
        seen += len(points)
        offset = result.get("next_page_offset")
        if not points or offset is None:
            return


def inspect(coll: str, url: str, cap: int) -> dict:
    """Newest ingest timestamp for one collection, plus coverage evidence."""
    best, field, dated, scanned = None, None, 0, 0
    for point in iter_points(coll, url, cap):
        scanned += 1
        if not isinstance(point, dict):
            continue
        flat = leaves(point.get("payload"))
        for name in INGEST_FIELDS:
            if name not in flat:
                continue
            got = parse_ts(flat[name])
            if got is not None:
                dated += 1
                if best is None or got > best:
                    best, field = got, name
                break
    return {
        "collection": coll,
        "newest": best.isoformat() if best else None,
        "field": field,
        "age_days": (datetime.now(timezone.utc) - best).days if best else None,
        "scanned": scanned,
        "dated": dated,
    }


def list_collections(url: str) -> list[str]:
    """Names of existing collections, minus the ones this check must not touch."""
    names = [c["name"] for c in qget("/collections", url).get("result", {}).get("collections", [])]
    return sorted(n for n in names if n not in EXCLUDED_NAMES and not n.endswith(EXCLUDED_SUFFIXES))


def main(argv: list[str] | None = None) -> int:
    """Run the check. Returns 0 fresh, 1 stale, 2 configuration error."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threshold-days", type=int, default=THRESHOLD_DAYS)
    ap.add_argument("--sample-cap", type=int, default=SAMPLE_CAP)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    try:
        rows = [inspect(c, args.url, args.sample_cap) for c in list_collections(args.url)]
    except QdrantError as exc:
        print("STALENESS-CHECK ERROR: %s" % exc, file=sys.stderr)
        return 2

    stale = [r for r in rows if r["age_days"] is not None and r["age_days"] > args.threshold_days]
    blind = [r for r in rows if r["age_days"] is None]
    if args.as_json:
        print(json.dumps({"threshold_days": args.threshold_days, "rows": rows}, indent=1))
        return 1 if stale else 0

    for row in sorted(stale, key=lambda r: -r["age_days"]):
        print(
            "STALE  %-26s newest=%s (%s) age=%sd  threshold=%sd"
            % (row["collection"], row["newest"], row["field"], row["age_days"], args.threshold_days)
        )
    for row in blind:
        print(
            "BLIND  %-26s no ingest timestamp in %d scanned docs"
            % (row["collection"], row["scanned"])
        )
    print(
        "\n%d collection(s) checked · %d stale · %d unmeasurable · threshold=%sd"
        % (len(rows), len(stale), len(blind), args.threshold_days)
    )
    if stale:
        print("\nA collection with no recent document is a DEAD FEED. Check its unit/cron:")
        print("  systemctl list-timers | grep -Ei 'rag|ingest'")
        print("  journalctl -u <unit> --since '-48 hours'")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())

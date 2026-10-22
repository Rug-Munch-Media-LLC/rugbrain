#!/usr/bin/env python3
"""Export the ``scan_intel`` Qdrant collection to ClickHouse ``wallet_memory``.

=====================================================================
DO NOT DELETE THE scan_intel 2560 BASE BECAUSE THIS SCRIPT SUCCEEDED.
=====================================================================
A successful export is necessary but NOT sufficient. The 2560 collection must
only ever be dropped by a human, after an independent field-by-field
verification against the ClickHouse copy. This script deletes nothing: it reads
Qdrant over read-only scroll/count endpoints and writes one new table.

WHY: scan_intel carries address/chain/doc_id/kind/risk_score/risk_level/
risk_flags/scanned_at and ZERO words of prose. It is looked up by address and
filtered by risk_score, never semantically searched. Re-embedding it at 4096
would mean inventing 52k sentences stamped ``text_source="reconstructed"`` -- a
permanent quality tax to make a count look green.

USAGE
    python scripts/export_address_labels.py               # dry run (default)
    python scripts/export_address_labels.py --limit 500   # dry run, 500 rows
    python scripts/export_address_labels.py --execute     # real write
    python scripts/export_address_labels.py --verify      # compare CH vs Qdrant

Requires CLICKHOUSE_USER/CLICKHOUSE_PASSWORD from the environment, falling back
to the CLICKHOUSE_* lines in .env.rag. Never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.clickhouse_labels import (
    SCAN_TABLE,
    get_client,
    insert_rows,
    label_counts,
    scan_row,
)
from rugbrain.clickhouse_store import (
    CLICKHOUSE_FALLBACK_DATABASE,
    ensure_table,
    load_env_rag,
    qualified,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("export_address_labels")

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6334").rstrip("/")
SOURCE_COLLECTION = "scan_intel"
PAGE, BATCH = 1000, 2000
_VERIFY_COLS = "address, chain_id, risk_score, risk_level, safety_score, risk_flags"
NOTICE = (
    "REMINDER: the scan_intel 2560 base is NOT deleted by this script and must "
    "NOT be deleted on the strength of it. Deletion is a separate human decision "
    "requiring independent field-by-field verification against ClickHouse."
)


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST to a Qdrant read endpoint, returning the unwrapped result. httpx
    rather than urllib so the scheme is fixed at the transport layer. Read
    endpoints only -- this script never issues a PUT or DELETE to Qdrant."""
    resp = httpx.post(f"{QDRANT_URL}{path}", json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()["result"]


def scroll_points(collection: str, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield every point's id+payload from Qdrant. READ ONLY."""
    offset: int | None = None
    while True:
        body: dict[str, Any] = {"limit": PAGE, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = _post(f"/collections/{collection}/points/scroll", body)
        for pt in result["points"]:
            yield pt
            if limit is not None:
                limit -= 1
                if limit <= 0:
                    return
        offset = result.get("next_page_offset")
        if offset is None:
            return


def source_count(collection: str = SOURCE_COLLECTION) -> int:
    return int(_post(f"/collections/{collection}/points/count", {"exact": True})["count"])


def iter_rows(limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Qdrant points -> export rows, skipping (and counting) unusable payloads."""
    skipped = 0
    for pt in scroll_points(SOURCE_COLLECTION, limit):
        row = scan_row(pt["id"], pt.get("payload") or {})
        if row is None:
            skipped += 1
            continue
        yield row
    if skipped:
        log.warning("skipped %d points with no address or no numeric risk_score", skipped)


def run_dry_run(limit: int | None) -> int:
    total, risk, sample = 0, {}, []
    for row in iter_rows(limit):
        total += 1
        lvl = row["risk_level"] or "(none)"
        risk[lvl] = risk.get(lvl, 0) + 1
        if len(sample) < 3:
            sample.append(
                {k: row[k] for k in ("address", "chain_id", "risk_score", "risk_level", "doc_id")}
            )
    print("\n=== DRY RUN - nothing written, nothing deleted ===")
    print(f"target           : {CLICKHOUSE_FALLBACK_DATABASE}.{SCAN_TABLE}")
    print(f"source           : qdrant://{QDRANT_URL}/collections/{SOURCE_COLLECTION}")
    print(f"rows to export   : {total}{' (limited)' if limit else ''}")
    print(f"risk_level split : {risk}")
    print("sample rows      :")
    for s in sample:
        print(f"  {s}")
    print(f"\n{NOTICE}")
    return total


def run_export(limit: int | None) -> int:
    cli = get_client(CLICKHOUSE_FALLBACK_DATABASE)
    if cli is None:
        log.error("no ClickHouse client - aborting without writing")
        return 0
    db = ensure_table(cli)
    if db is None:
        log.error(
            "could not create table in %s - aborting without writing", CLICKHOUSE_FALLBACK_DATABASE
        )
        return 0
    print(f"=== WRITING to {db}.{SCAN_TABLE} (replaces rows with the same row_id) ===")
    print(NOTICE)
    inserted, buf, started = 0, [], time.monotonic()
    for row in iter_rows(limit):
        buf.append(row)
        if len(buf) >= BATCH:
            inserted += insert_rows(cli, db, buf)
            buf = []
            if inserted % 10000 == 0:
                log.info("inserted %d (%.1fs)", inserted, time.monotonic() - started)
    inserted += insert_rows(cli, db, buf)
    elapsed = time.monotonic() - started
    print(f"inserted {inserted} rows in {elapsed:.1f}s ({inserted / max(elapsed, 0.001):.0f}/s)")
    print("ClickHouse ReplacingMergeTree merges asynchronously; run --verify after a short wait.")
    return inserted


def run_verify(sample: int) -> int:
    """Field-by-field comparison of N Qdrant points against the ClickHouse copy."""
    cli = get_client(CLICKHOUSE_FALLBACK_DATABASE)
    if cli is None:
        log.error("no ClickHouse client - cannot verify")
        return 1
    try:
        cli.command(f"OPTIMIZE TABLE {qualified()} FINAL")
        log.info("OPTIMIZE FINAL issued (dedup completed)")
    except Exception as exc:
        log.warning("OPTIMIZE skipped: %s", exc)
    counts, src = label_counts(), source_count()
    print("=== VERIFY ===")
    print(f"qdrant points (exact)      : {src}")
    print(f"clickhouse rows            : {counts.get('rows', 'n/a')}")
    print(f"clickhouse distinct addr   : {counts.get('distinct_addresses', 'n/a')}")
    mismatches = checked = 0
    fields = ("address", "chain_id", "risk_score", "risk_level", "safety_score", "risk_flags")
    for pt in scroll_points(SOURCE_COLLECTION, sample):
        row = scan_row(pt["id"], pt.get("payload") or {})
        if row is None:
            continue
        sql = f"SELECT {_VERIFY_COLS} FROM {qualified()} FINAL WHERE row_id = %(rid)s"  # noqa: S608
        got = cli.query(sql, parameters={"rid": row["row_id"]}).result_rows
        checked += 1
        if not got:
            mismatches += 1
            log.error("MISSING row_id=%s address=%s", row["row_id"][:12], row["address"])
            continue
        expect = tuple(row[f] for f in fields)
        if tuple(got[0]) != expect:
            mismatches += 1
            for fname, e, a in zip(fields, expect, got[0], strict=True):
                if e != a:
                    log.error("MISMATCH %s: qdrant=%r clickhouse=%r", fname, e, a)
    rate = (mismatches / checked * 100) if checked else 0.0
    print(f"spot-checked {checked} records field-by-field over {len(fields)} fields")
    print(f"mismatches: {mismatches} ({rate:.2f}%)")
    print(
        f"check method: deterministic row_id (sha256(doc_id)[:32]) looked up with FINAL, "
        f"compared field-by-field on {', '.join(fields)}"
    )
    return mismatches


def main() -> int:
    ap = argparse.ArgumentParser(description="Export scan_intel labels to ClickHouse")
    ap.add_argument("--execute", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--verify", action="store_true", help="compare ClickHouse against Qdrant")
    ap.add_argument("--limit", type=int, default=None, help="cap rows (for smoke tests)")
    ap.add_argument("--sample", type=int, default=500, help="records to spot-check in --verify")
    args = ap.parse_args()

    load_env_rag()
    if args.verify:
        return 1 if run_verify(args.sample) else 0
    if args.execute:
        run_export(args.limit)
        return 0
    run_dry_run(args.limit)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except httpx.HTTPError as exc:
        log.error(
            "qdrant request failed at %s: %s",
            hashlib.sha256(QDRANT_URL.encode()).hexdigest()[:8],
            type(exc).__name__,
        )
        raise SystemExit(2) from None

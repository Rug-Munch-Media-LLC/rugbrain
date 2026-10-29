"""CLI: ``python -m rugbrain.zeroday probe|run|surface``.

``probe``  — poll every enabled source, print counts and latency (evidence).
``run``    — probe + dedupe + persist snapshot/feed/latency + alerts.
``surface``— compact read of the stored records (same data the MCP tools serve).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from rugbrain.zeroday.alerts import send_batch
from rugbrain.zeroday.pipeline import merge
from rugbrain.zeroday.registry import SPECS, fetch_all
from rugbrain.zeroday.schema import SEVERITY_LEVEL, Record
from rugbrain.zeroday.store import Store


def _print_probe(results: list[dict[str, Any]]) -> None:
    width = max(len(str(r["source"])) for r in results)
    for row in results:
        status = "ok" if row["ok"] else f"FAIL {row['error']}"
        print(
            f"{row['source']!s:<{width}}  {row['records']:>4} rec  {row['elapsed_ms']:>6} ms  {status}"
        )


def _filter(
    rows: list[dict[str, Any]], kind: str | None, min_severity: str | None, limit: int
) -> list[dict[str, Any]]:
    floor = SEVERITY_LEVEL.get(min_severity or "unknown", -1)
    kept = [
        row
        for row in rows
        if (kind is None or row.get("kind") == kind)
        and SEVERITY_LEVEL.get(str(row.get("severity")), -1) >= floor
    ]
    kept.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
    return kept[:limit]


async def _probe(limit: int, only: list[str] | None) -> int:
    results = await fetch_all(limit=limit, only=only)
    _print_probe([r.as_dict() for r in results])
    total = sum(len(r.records) for r in results)
    print(f"TOTAL {total} records from {sum(1 for r in results if r.ok)}/{len(results)} sources")
    return 0 if any(r.ok for r in results) else 1


async def _run(limit: int, only: list[str] | None) -> int:
    store = Store()
    results = await fetch_all(limit=limit, only=only)
    _print_probe([r.as_dict() for r in results])
    records: list[Record] = [rec for r in results for rec in r.records]
    outcome = merge(store.load_state(), records)
    store.save_state(outcome.state)
    store.write_snapshot([rec.as_dict() for rec in records])
    store.append_feed([c.as_dict() for c in outcome.changes])
    store.write_latency(outcome.latency)
    sent = await send_batch(list(outcome.changes), outcome.state)
    store.save_state(outcome.state)
    print(
        json.dumps(
            {
                "records": len(records),
                "changes": len(outcome.changes),
                "clusters": outcome.latency["clusters"],
                "alerts_sent": len(sent),
                "store": str(store.root),
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rugbrain.zeroday")
    parser.add_argument("command", choices=("probe", "run", "surface"), nargs="?", default="probe")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--only", default="")
    parser.add_argument("--kind", default=None)
    parser.add_argument("--min-severity", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    only = [name.strip() for name in args.only.split(",") if name.strip()] or None

    if args.command == "surface":
        rows = _filter(Store().read_records(), args.kind, args.min_severity, args.limit)
        print(json.dumps({"count": len(rows), "items": rows}, indent=1, sort_keys=True))
        return 0
    if args.command == "run":
        return asyncio.run(_run(args.limit, only))
    return asyncio.run(_probe(args.limit, only))


__all__ = ["SPECS", "main"]

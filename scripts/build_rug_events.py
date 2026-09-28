#!/usr/bin/env python3
"""Build the ``rug_events`` collection: project, embed, upsert, index.

Operator entrypoint for the collection this repo lacked. Reads the rug
telemetry ALREADY in ``defi_exploits``/``exploits`` (read-only) and writes the
structured projection into ``rug_events``.

Usage::

    python scripts/build_rug_events.py --dry-run          # project + report
    python scripts/build_rug_events.py --apply             # create + write
    python scripts/build_rug_events.py --apply --scratch rug_events_smoke
    python scripts/build_rug_events.py --sources-only      # scythe only, no embed

``--scratch`` writes to a DIFFERENT collection name and never touches
``rug_events``; it exists so the write path can be smoke-tested without
committing to a production write.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rugbrain.rug_events.build_events import project_collection  # noqa: E402
from rugbrain.rug_events.builder import apply_indexes, ensure_collection  # noqa: E402
from rugbrain.rug_events.deployers import serial_ruggers  # noqa: E402
from rugbrain.rug_events.qdrant_io import DEFAULT_URL, upsert_points  # noqa: E402
from rugbrain.rug_events.schema import COLLECTION  # noqa: E402

BATCH = 256


def summarize(events: list[dict]) -> dict:
    """Counts a human can check the build against."""
    dated = [e for e in events if e.get("rug_ts")]
    amounts = [e["liq_removed_usd"] for e in events if e.get("liq_removed_usd")]
    holders = [e["holders_at_rug"] for e in events if e.get("holders_at_rug")]
    return {
        "events": len(events),
        "dated": len(dated),
        "undated": len(events) - len(dated),
        "with_liq_removed": len(amounts),
        "total_liq_removed_usd": round(sum(amounts), 2),
        "with_holders": len(holders),
        "by_source": _count(e.get("source") for e in events),
        "by_chain": _count(e.get("chain") for e in events),
        "serial_ruggers": serial_ruggers(events),
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = str(v or "unknown")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed with the repo's own query embedder (same model as the corpus)."""
    from rugbrain.query_embedder import embed_query

    vectors: list[list[float]] = []
    for text in texts:
        vectors.append(_run_coro(embed_query(text)))
    return vectors


def _run_coro(coro) -> list[float]:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("embed_batch must not be called from a running loop")


def render(event: dict) -> str:
    """The embedded text. Says what happened, with the numbers inline."""
    parts = [
        f"Rug event: token {event.get('token') or event.get('token_address')}",
        f"Chain: {event.get('chain')}",
    ]
    if event.get("rug_ts"):
        parts.append(f"Date: {event['rug_ts']}")
    if event.get("liq_removed_usd") is not None:
        parts.append(f"Total removed liquidity: {event['liq_removed_usd']} USD")
    if event.get("liq_added_usd") is not None:
        parts.append(f"Total added liquidity: {event['liq_added_usd']} USD")
    if event.get("rug_ratio") is not None:
        parts.append(f"Rug ratio (removed/added): {event['rug_ratio']}")
    if event.get("holders_at_rug") is not None:
        parts.append(f"Holders at rug: {event['holders_at_rug']}")
    if event.get("deployer"):
        parts.append(
            f"Deployer: {event['deployer']} (prior rugs: {event.get('deployer_prior_rugs', 0)})"
        )
    for key, label in (
        ("rug_mechanism", "Mechanism"),
        ("rug_outcome", "Outcome"),
        ("loss_usd", "Documented loss USD"),
        ("early_warning", "Early warning"),
    ):
        if event.get(key):
            parts.append(f"{label}: {event[key]}")
    parts.append(f"Source: {event.get('source')} ({event.get('source_url')})")
    return "\n".join(parts)


def write_events(events: list[dict], base: str, collection: str) -> int:
    """Embed in batches and upsert; returns the number of points written."""
    written = 0
    for start in range(0, len(events), BATCH):
        batch = events[start : start + BATCH]
        vectors = embed_batch([render(e) for e in batch])
        points = [
            {"id": e["event_id"], "vector": v, "payload": {**e, "content": render(e)}}
            for e, v in zip(batch, vectors, strict=True)
        ]
        upsert_points(base, collection, points)
        written += len(points)
        print(f"  upserted {written}/{len(events)}")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build-rug-events", description=__doc__)
    ap.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL", DEFAULT_URL))
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--scratch", metavar="NAME", help="write to NAME instead of rug_events")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = args.scratch or COLLECTION
    if args.scratch:
        print(f"NOTE: scratch mode — writing to '{target}', NOT '{COLLECTION}'")

    print(f"projecting from {args.qdrant_url} ...")
    events = project_collection(args.qdrant_url)
    summary = summarize(events)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if not args.apply:
        print("\ndry run — pass --apply to write")
        return 0

    ensure_collection(args.qdrant_url, target, dry_run=False)
    written = write_events(events, args.qdrant_url, target)
    for row in apply_indexes(args.qdrant_url, target):
        print(f"  index {row['field']}: {row['action']}")
    print(f"\nwrote {written} rug events to '{target}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the ``rug_events`` collection: project, embed, upsert, index.

Operator entrypoint for the collection this repo lacked. Reads the rug
telemetry ALREADY in ``defi_exploits``/``exploits`` (read-only) and writes the
structured projection into ``rug_events``.

Usage::

    python scripts/build_rug_events.py                    # project + report only
    python scripts/build_rug_events.py --apply            # create + write
    python scripts/build_rug_events.py --apply --scratch rug_events_scratch2

**A run is a dry run by default** — projecting and reporting touches nothing.
``--apply`` is the only thing that writes, so a plain invocation cannot mutate
the corpus. (There is deliberately no ``--dry-run`` flag: dry run is the
absence of ``--apply``, and a flag that only restates the default is a way to
let a production write through unnoticed.)

``--scratch`` writes to a DIFFERENT collection name and never touches
``rug_events``; it exists so the write path can be verified end to end before
committing to a production write.

The embed lane is pinned to the corpus's 2560-dim Qwen3-Embedding-4B before the
first request (``rug_events.embedding``); run with the env sourced, e.g.
``set -a; . /srv/secrets/rugbrain.env; set +a``, or the key is absent and every
call fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Pin the embed lane BEFORE importing anything from ``rugbrain``.
#
# ``import rugbrain.rug_events.embedding`` executes ``rugbrain/__init__.py``
# first, which imports ``qwen_embedders``, which reads DEEPINFRA_EMBED_URL and
# DEEPINFRA_EMBED_MODEL at *import* time into module constants. Setting them
# afterwards is a silent no-op — every call then 404s on the doubled
# ``/v1/embeddings/embeddings`` path. So the pin has to be plain os.environ
# here, ahead of the first rugbrain import, not a call into the package.
# Values are asserted against the module's own LANE_ENV below, so the two
# cannot drift apart silently.
for _key, _value in (
    ("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1"),
    ("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B"),
    ("RAG_EMBED_MODEL", "qwen3-embedding-4b"),
    ("QWEN_EMBED_DIMS", "2560"),
):
    os.environ[_key] = _value

from rugbrain.rug_events.embedding import (  # noqa: E402
    CORPUS_DIM,
    LANE_ENV,
    DimMismatch,
    embed_sync,
)
from rugbrain.rug_events.build_events import project_collection  # noqa: E402
from rugbrain.rug_events.builder import apply_indexes, ensure_collection  # noqa: E402
from rugbrain.rug_events.deployers import serial_ruggers  # noqa: E402
from rugbrain.rug_events.qdrant_io import (  # noqa: E402
    DEFAULT_URL,
    collection_info,
    point_id,
    upsert_points,
)
from rugbrain.rug_events.schema import COLLECTION  # noqa: E402

# The prologue above and LANE_ENV must agree; a mismatch means the script pins
# a lane the module does not know about, which is the failure this file exists
# to prevent repeating.
assert LANE_ENV == dict(
    _LANE_PROLOGUE := {
        "DEEPINFRA_EMBED_URL": os.environ["DEEPINFRA_EMBED_URL"],
        "DEEPINFRA_EMBED_MODEL": os.environ["DEEPINFRA_EMBED_MODEL"],
        "RAG_EMBED_MODEL": os.environ["RAG_EMBED_MODEL"],
        "QWEN_EMBED_DIMS": os.environ["QWEN_EMBED_DIMS"],
    }
), f"embed lane prologue drifted from LANE_ENV: {_LANE_PROLOGUE} != {LANE_ENV}"

BATCH = 128


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
    """Embed in pages and upsert; returns the number of points written."""
    written = 0
    for start in range(0, len(events), BATCH):
        batch = events[start : start + BATCH]
        # ONE request per page via embed() — embed_one() in a loop is 1,877
        # round-trips, which is what made the first build take minutes.
        vectors = embed_sync([render(e) for e in batch])
        points = [
            # point_id: Qdrant takes only int/UUID ids; event_id stays in the
            # payload so the natural key remains auditable.
            {"id": point_id(e["event_id"]), "vector": v, "payload": {**e, "content": render(e)}}
            for e, v in zip(batch, vectors, strict=True)
        ]
        upsert_points(base, collection, points)
        written += len(points)
        print(f"  upserted {written}/{len(events)}")
    return written


def verify(base: str, collection: str, expected: int) -> dict:
    """Check the ARTIFACT, not the exit code: point count and vector dim."""
    info = collection_info(base, collection) or {}
    cfg = (info.get("config") or {}).get("params") or {}
    vectors = cfg.get("vectors") or {}
    actual = int(info.get("points_count") or 0)
    return {
        "collection": collection,
        "status": info.get("status"),
        "points_count": actual,
        "expected": expected,
        "matches": actual == expected,
        "dim": vectors.get("size") if isinstance(vectors, dict) else None,
        "dim_ok": (vectors.get("size") if isinstance(vectors, dict) else None) == CORPUS_DIM,
        "payload_indexes": sorted((info.get("payload_schema") or {}).keys()),
    }


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

    check = verify(args.qdrant_url, target, written)
    print(f"\nwrote {written} rug events to '{target}'")
    print(json.dumps(check, indent=2, ensure_ascii=False))
    if not (check["matches"] and check["dim_ok"]):
        print(f"VERIFY FAILED for '{target}' — artifact does not match the build", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

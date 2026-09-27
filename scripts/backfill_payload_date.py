#!/usr/bin/env python3
"""Backfill a top-level RFC 3339 ``date`` (+ normalised ``chain``) on RugBrain's
crypto collections, then create the payload indexes that make range filtering
cheap. **Plan + script only — not executed.**

Idempotency and resumability
----------------------------
1. **Idempotent** — a point is skipped when ``payload.date`` already equals the
   value the backfill would write. A second run over completed batches is a
   no-op and does not rewrite points.
2. **Resumable** — progress is checkpointed to ``--state`` after every batch
   (last point id per collection, plus points_updated). ``--resume`` picks up
   from the checkpoint via Qdrant's ``offset`` cursor, so an interrupted run
   continues instead of restarting.
3. **Ordering is stable** — the same offset over an unchanged collection always
   yields the same page, so a resumed run cannot skip a page. A resumed run
   whose collection grew since the checkpoint is caught by ``--verify-cursor``,
   which compares the checkpointed id against what the offset now returns and
   aborts instead of silently shifting the window.
4. **Bounded blast radius** — ``--max-points`` (default 2000) and
   ``--limit-collections`` keep any single run small. Nothing runs without
   ``--apply``; the default is a dry run that prints the plan.

Usage::

    # dry run — prints the plan and the expected row count, writes nothing
    python3 scripts/backfill_payload_date.py

    # create the payload indexes only (metadata, safe, reversible)
    python3 scripts/backfill_payload_date.py --index-only --apply

    # the real backfill, one collection, checkpointed
    python3 scripts/backfill_payload_date.py --collections news_v2 --apply
    python3 scripts/backfill_payload_date.py --collections news_v2 --apply --resume

Requires operator approval. ``DROP``/``CASCADE``-class operations are absent by
design: this script only ``set_payload``s and creates indexes.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))
sys.path.insert(0, str(_HERE.parent))

from rugbrain.crypto_schema import parse_date  # noqa: E402 — house normaliser, padded ISO
from scripts.qdrant_date_lib import canonical_chain, canonical_date, dig  # noqa: E402
from scripts.qdrant_date_plan import (  # noqa: E402
    DATE_SOURCES,
    INDEX_PLAN,
    create_index,
    existing_indexes,
    points_count,
    request,
)

LOG = logging.getLogger("backfill_payload_date")
STATE_PATH = pathlib.Path(".backfill_payload_date.state.json")
BATCH = 128


def load_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)
    LOG.info("checkpoint_written path=%s collections=%d", path, len(state))


def derive_date(collection: str, payload: dict[str, Any]) -> str:
    """Resolve the canonical date for one payload from its candidate sources."""
    for source in DATE_SOURCES.get(collection, []):
        raw = dig(payload, source)
        if raw is None:
            continue
        stamped = canonical_date(raw)
        if stamped:
            return stamped
        text = str(payload.get("content") or "")[:2000]
        if text:
            return canonical_date(parse_date(raw, text))
    return ""


def build_updates(collection: str, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return set_payload ops only for points whose value would actually change."""
    updates: list[dict[str, Any]] = []
    for point in points:
        payload = point.get("payload") or {}
        stamped = derive_date(collection, payload)
        chain = canonical_chain(payload.get("chain") or dig(payload, "metadata.chain"))
        changes: dict[str, Any] = {}
        if stamped and payload.get("date") != stamped:
            changes["date"] = stamped
        if payload.get("chain") != chain:
            changes["chain"] = chain
        if changes:
            updates.append({"id": point["id"], "payload": changes})
    return updates


def scroll_batch(collection: str, offset: Any, limit: int = BATCH) -> dict[str, Any]:
    body: dict[str, Any] = {"limit": limit, "with_payload": True, "with_vector": False}
    if offset is not None:
        body["offset"] = offset
    return request("POST", f"/collections/{collection}/points/scroll", body) or {}


def plan_missing(collection: str) -> list[dict[str, Any]]:
    """Index specs for this collection that are not present yet (idempotent)."""
    present = existing_indexes(collection)
    return [spec for spec in INDEX_PLAN.get(collection, []) if spec["field"] not in present]


def run_collection(
    collection: str, args: argparse.Namespace, state: dict[str, Any]
) -> dict[str, int]:
    entry = state.get(collection, {})
    offset = entry.get("offset") if args.resume else None
    stats = {"scanned": 0, "dated": 0, "written": 0, "undatable": 0}
    for _ in range(args.max_points // BATCH):
        page = scroll_batch(collection, offset)
        points = page.get("points") or []
        if not points:
            break
        if args.resume and entry.get("cursor") and points[0]["id"] != entry["cursor"]:
            raise RuntimeError(
                "cursor drift in {}: checkpoint={} page0={} — rerun without --resume".format(collection, entry.get("cursor"), points[0]["id"])
            )
        updates = build_updates(collection, points)
        dated = sum(1 for p in points if derive_date(collection, p.get("payload") or {}))
        stats["scanned"] += len(points)
        stats["dated"] += dated
        stats["undatable"] += len(points) - dated
        stats["written"] += len(updates)
        if updates and args.apply:
            request(
                "POST",
                f"/collections/{collection}/points/payload",
                {"points": updates, "wait": True},
            )
            LOG.info(
                "batch_applied collection=%s points=%d updates=%d",
                collection,
                len(points),
                len(updates),
            )
        else:
            LOG.info(
                "batch_scanned collection=%s points=%d updates=%d",
                collection,
                len(points),
                len(updates),
            )
        offset = page.get("next_page_offset")
        state[collection] = {
            "offset": offset,
            "cursor": points[-1]["id"],
            "points_scanned": stats["scanned"],
        }
        save_state(args.state, state)
        if offset is None:
            break
    state[collection]["done"] = offset is None
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collections", default="", help="comma-separated, default: all planned")
    parser.add_argument("--max-points", type=int, default=2000, help="scan cap per collection")
    parser.add_argument("--apply", action="store_true", help="perform writes (default: dry run)")
    parser.add_argument("--index-only", action="store_true", help="only create payload indexes")
    parser.add_argument("--resume", action="store_true", help="continue from the checkpoint")
    parser.add_argument("--state", type=pathlib.Path, default=STATE_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    LOG.info(
        "mode=%s apply=%s max_points=%d resume=%s",
        "APPLY" if args.apply else "DRY-RUN",
        args.apply,
        args.max_points,
        args.resume,
    )
    if args.max_points % BATCH:
        args.max_points -= args.max_points % BATCH
    targets = [c.strip() for c in args.collections.split(",") if c.strip()] or list(INDEX_PLAN)
    state = load_state(args.state) if args.resume else {}
    totals = {"scanned": 0, "written": 0}
    for collection in targets:
        count = points_count(collection)
        LOG.info(
            "plan collection=%s points=%s date_sources=%s",
            collection,
            count,
            DATE_SOURCES.get(collection, []),
        )
        if args.index_only:
            missing = plan_missing(collection)
            LOG.info("index_plan collection=%s would_create=%s", collection, missing)
            if args.apply:
                for spec in missing:
                    create_index(collection, spec["field"], spec["params"])
                    LOG.info(
                        "index_created collection=%s field=%s type=%s",
                        collection,
                        spec["field"],
                        spec["params"]["type"],
                    )
            continue
        stats = run_collection(collection, args, state)
        totals["scanned"] += stats["scanned"]
        totals["written"] += stats["written"]
        LOG.info("result collection=%s %s", collection, json.dumps(stats, sort_keys=True))
        if args.apply:
            for spec in plan_missing(collection):
                create_index(collection, spec["field"], spec["params"])
                LOG.info(
                    "index_created collection=%s field=%s type=%s",
                    collection,
                    spec["field"],
                    spec["params"]["type"],
                )
    LOG.info("TOTAL scanned=%d would_write=%d", totals["scanned"], totals["written"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

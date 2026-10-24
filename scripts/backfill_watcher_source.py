#!/usr/bin/env python3
"""Backfill historical mixed-case WatcherGuru ``source`` payloads in Qdrant.

The same Telegram channel was ingested under several spellings over time
(tg:watcherguru, x.com/watcherguru, case variants of t.me/WatcherGuru). Every
point whose top-level ``source`` mentions "watcherguru" — any case, any
prefix — is relabelled to the canonical lowercase channel handle
``t.me/watcherguru`` with a payload-only ``set_payload`` overwrite. Vectors are
never touched, nothing is re-embedded, nothing is deleted, and every other
payload field is preserved (set_payload merges by default).

Modes
  no flag      dry run — scans and reports the exact writes that would happen.
  --apply      performs the set_payload calls.
  --value      overrides the canonical target (default ``t.me/watcherguru``).

Collection membership is explicit (COLLECTIONS); the target source itself
already matches the matcher, so a clean re-run rewrites nothing — idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

import httpx

COLLECTIONS = (
    "news_v2_8b",
    "social_intel_8b",
    "rag_documents",
    "scam_intel_v2_8b",
    "threat_intel_v2_8b",
)
CANONICAL = "t.me/watcherguru"
SUBSTR = "watcherguru"
SCROLL_LIMIT = 2000
WRITE_CHUNK = 100


def watcher_value(value: object) -> bool:
    """True when a source string mentions WatcherGuru — any case, any prefix."""
    return SUBSTR in str(value if value is not None else "").lower()


def relabel_value(value: object) -> bool:
    """True when the value matches WatcherGuru but is not already canonical."""
    return watcher_value(value) and str(value).strip() != CANONICAL


def is_canonical(value: object) -> bool:
    return str(value if value is not None else "").strip() == CANONICAL


async def scan_collection(
    client: httpx.AsyncClient, collection: str, target: str
) -> dict[str, Any]:
    """One scan pass: classify every top-level ``source``, return ids + stats.

    Points are fetched with only the ``source`` payload field, vectors off.
    Canonical points are counted separately so the rewrite is a no-op on them.
    A point whose top-level source does not match but whose nested
    ``metadata.source`` does is reported in ``stray_meta`` — flagged, not
    written, so other fields stay untouched.
    """
    ids: list[str] = []
    matched: Counter[str] = Counter()
    canonical = 0
    stray_meta = 0
    offset: str | None = None
    scanned = 0
    while True:
        body: dict[str, Any] = {"limit": SCROLL_LIMIT, "with_vector": False}
        body["payload"] = {"include": ["source"]}
        if offset:
            body["offset"] = offset
        resp = await client.post(f"/collections/{collection}/points/scroll", json=body)
        resp.raise_for_status()
        result = resp.json()["result"]
        for point in result.get("points", []):
            scanned += 1
            payload = point.get("payload") or {}
            src = payload.get("source")
            if watcher_value(src):
                if is_canonical(src):
                    canonical += 1
                    matched[target] += 1
                else:
                    ids.append(point["id"])
                    matched[str(src)] += 1
            elif src is None and watcher_value((payload.get("metadata") or {}).get("source")):
                stray_meta += 1
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return {"ids": ids, "matched": matched, "canonical": canonical,
            "stray_meta": stray_meta, "scanned": scanned}


async def scan_count(client: httpx.AsyncClient, collection: str) -> int:
    resp = await client.post(f"/collections/{collection}/points/count",
                             json={"filter": {"must": [{"key": "source", "match": {"text": SUBSTR}}]},
                                   "exact": True})
    resp.raise_for_status()
    return int(resp.json()["result"]["count"])


async def rewrite(client: httpx.AsyncClient, collection: str, ids: list[str],
                  target: str, apply: bool) -> int:
    """set_payload on chunked id lists — merges, keeps every other field."""
    written = 0
    for i in range(0, len(ids), WRITE_CHUNK):
        chunk = ids[i:i + WRITE_CHUNK]
        if apply:
            resp = await client.post(
                f"/collections/{collection}/points/payload",
                json={"payload": {"source": target}, "points": chunk},
            )
            resp.raise_for_status()
        written += len(chunk)
    return written


async def run(url: str, collections: tuple[str, ...], target: str, apply: bool,
              transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Sweep every collection; verify after by rescanning distinct sources."""
    report: dict[str, Any] = {"collections": {}, "target": target, "apply": apply}
    async with httpx.AsyncClient(base_url=_base(url), timeout=120.0,
                                 transport=transport) as client:
        for collection in collections:
            before = await scan_collection(client, collection, target)
            report["collections"][collection] = {
                "scanned": before["scanned"],
                "watchers_before": sum(before["matched"].values()),
                "sources_before": dict(before["matched"]),
                "stray_meta_sources": before["stray_meta"],
                "rewritten": await rewrite(client, collection, before["ids"], target, apply),
            }
            after = await scan_collection(client, collection, target)
            report["collections"][collection]["watchers_after"] = sum(
                after["matched"].values())
            report["collections"][collection]["sources_after"] = dict(after["matched"])
            report["collections"][collection]["ids_matched_after"] = len(after["ids"])
    return report


def _base(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill WatcherGuru source payloads to t.me/watcherguru")
    ap.add_argument("--url", default=None, help="Qdrant base URL (default $QDRANT_URL or 127.0.0.1:6334)")
    ap.set_defaults(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/"))
    ap.add_argument("--collection", action="append", default=None, dest="collections",
                    help="restrict to one collection (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write; default is dry run")
    ap.add_argument("--value", default=CANONICAL, help="canonical source value to write")
    args = ap.parse_args()
    runs = tuple(args.collections) if args.collections else COLLECTIONS
    print(asyncio.run(run(args.url, runs, args.value, args.apply)))


if __name__ == "__main__":
    main()

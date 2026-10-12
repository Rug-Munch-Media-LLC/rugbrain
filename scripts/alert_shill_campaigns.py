"""Emit NEW shill-campaign findings from scam_patterns_8b as alerts.

The publisher side (rugmunchbot) reads this stream and announces campaigns. To
guarantee "no duplicate announcements", this emitter is watermark-based: it
tracks the newest ingested_at it has emitted and only ever emits strictly newer
findings, plus a per-item seen-set so a backfill never double-emits.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

COLLECTION = "scam_patterns_8b"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
STATE = Path(os.getenv("SHILL_ALERT_STATE", "/var/lib/rugbrain/shill_alert_state.json"))
OUT = Path(os.getenv("SHILL_ALERT_STREAM", "/var/lib/rugbrain/shill_alerts.jsonl"))


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cursor": "", "seen": []}


def save_state(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")


def emit(items: list[dict[str, Any]], state: dict[str, Any], *, dry_run: bool) -> int:
    """Filter to unseen+newer, append to the stream, advance the cursor."""
    cursor = state.get("cursor") or ""
    seen = set(state.get("seen") or [])
    # When the cursor is unset everything unseen is new; afterwards strictly newer.
    fresh = [
        it for it in items
        if it["id"] not in seen and ((not cursor) or (it.get("ingested_at") or "") > cursor)
    ]
    fresh.sort(key=lambda it: it.get("ingested_at") or "")
    if fresh and not dry_run:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as fh:
            for it in fresh:
                fh.write(json.dumps(it, sort_keys=True) + "\n")
        state["cursor"] = max(it.get("ingested_at") or "" for it in fresh)
        state["seen"] = (list(seen) + [it["id"] for it in fresh])[-5000:]
        save_state(state)
    for it in fresh:
        m = it["metadata"]
        print(f"ALERT risk={m.get('risk')} cat={','.join(m.get('categories', []))} "
              f"ch={m.get('channel')} {it['text'][:70]}")
    return len(fresh)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--min-risk", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL, timeout=60)
    points, offset = [], None
    flt = {"must": [{"key": "metadata.kind", "match": {"value": "shill_campaign"}}]}
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION, scroll_filter=flt, limit=args.limit,
            with_payload=True, with_vectors=False, offset=offset,
        )
        if not batch:
            break
        points.extend(batch)
        if offset is None:
            break
    items = [
        {"id": str(p.id), "text": (p.payload or {}).get("text", ""),
         "metadata": (p.payload or {}).get("metadata", {}),
         "ingested_at": str(getattr(p, "last_update_time", "") or (p.payload or {}).get("metadata", {}).get("ingested_at", ""))}
        for p in points
    ]
    items = [it for it in items if int(it["metadata"].get("risk", 0)) >= args.min_risk]
    state = load_state()
    n = emit(items, state, dry_run=args.dry_run)
    print(f"scanned={len(items)} emitted={n} cursor={state.get('cursor') or ''}")


if __name__ == "__main__":
    main()

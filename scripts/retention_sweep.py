"""retention_sweep.py — per-source TTL enforcement for RAG collections.

RAG collections grow forever by default: social chatter decays in days, news
loses relevance in months, while indicators (scams, wallets, blacklists),
audits, exploits and education are permanent reference material. This sweep
deletes points older than their collection's TTL.

TTL config lives in ``RETENTION`` below (collection → days; ``None`` = keep
forever). Per-source overrides are supported for the mixed ``rag_documents``
collection via ``SOURCE_TTL``.

Usage:
    python3 scripts/retention_sweep.py --dry-run     # report only (default)
    python3 scripts/retention_sweep.py --apply       # delete expired points
    python3 scripts/retention_sweep.py --apply --collection social_intel

Deployed as rugbrain-retention.timer (nightly 04:10 UTC).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
BATCH = 2000
TS_FIELDS = ("updated_at", "created_at", "timestamp", "ingested_at", "date", "ts", "published")

#: collection -> retention in days (None = permanent reference material).
RETENTION: dict[str, int | None] = {
    "news_v2": 90,
    "news_articles": 90,
    "social_intel": 30,
    "rag_documents": 90,          # overridden per content_type below
    "market_health": 180,
    "stablecoin_market": 180,
    "live_threats": 30,
    "security_alerts": 90,
    "security_feeds": 180,
    # permanent (explicit for documentation value):
    "scam_intel_v2": None, "known_scams": None, "scam_patterns": None,
    "wallet_profiles": None, "wallet_v2": None,
    "defi_exploits": None, "threat_intel_v2": None, "hacks_v2": None,
    "contract_audits": None, "github_audits": None, "crypto_vulnerabilities": None,
    "regulation_enforcement": None, "research_v2": None, "education": None,
    "codebase_reference_v2": None, "exploits": None, "security_patterns": None,
    "master_skills": None, "protocol_design": None, "cross_chain": None,
}

#: rag_documents content_type -> days (tighter than the collection default).
SOURCE_TTL: dict[str, int] = {"social": 30, "news": 90}


def _post(path: str, body: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://, audited 2026-09-20
        f"{QDRANT}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — audited 2026-09-20
        return json.load(resp)


def _ts_of(payload: dict) -> datetime | None:
    """Newest timestamp in the payload — top-level or nested in ``metadata``."""
    best: int | None = None
    sources = [payload]
    md = payload.get("metadata")
    if isinstance(md, dict):
        sources.append(md)
    for source in sources:
        for f in TS_FIELDS:
            v = source.get(f)
            if isinstance(v, (int, float)) and v > 1_000_000_000:
                best = int(v) if best is None else max(best, int(v))
            elif isinstance(v, str) and len(v) >= 10:
                try:
                    ts = int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
                    best = ts if best is None else max(best, ts)
                except ValueError:
                    continue
    return datetime.fromtimestamp(best, tz=UTC) if best else None


def sweep(collection: str, ttl_days: int, apply: bool, per_source: dict[str, int] | None = None) -> tuple[int, int]:
    """Delete points older than ttl_days. Returns (scanned, deleted)."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=ttl_days)
    offset, scanned, expired = None, 0, []
    while True:
        body: dict = {"limit": BATCH, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = _post(f"/collections/{collection}/points/scroll", body)["result"]
        points = res.get("points") or []
        if not points:
            break
        for pt in points:
            scanned += 1
            pl = pt.get("payload") or {}
            days = ttl_days
            if per_source:
                ct = str(pl.get("content_type") or "").lower()
                days = per_source.get(ct, ttl_days)
            ts = _ts_of(pl)
            if ts is not None and ts < datetime.now(tz=UTC) - timedelta(days=days):
                expired.append(str(pt["id"]))
        offset = res.get("next_page_offset")
        if offset is None:
            break
    if apply and expired:
        for i in range(0, len(expired), 500):
            _post(f"/collections/{collection}/points/delete?wait=true", {"points": expired[i : i + 500]})
    return scanned, len(expired)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="", help="single collection (default: all with a TTL)")
    ap.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    args = ap.parse_args()

    targets = {args.collection: RETENTION.get(args.collection, 90)} if args.collection else {
        c: d for c, d in RETENTION.items() if d is not None
    }
    total_expired = 0
    for collection, days in targets.items():
        if days is None:
            continue
        per_source = SOURCE_TTL if collection == "rag_documents" else None
        scanned, expired = sweep(collection, days, args.apply, per_source)
        total_expired += expired
        state = "deleted" if args.apply else "expired"
        print(f"  {collection:22} ttl={days:>3}d  scanned={scanned:>7,}  {state}={expired:>6,}")
    mode = "APPLIED" if args.apply else "dry-run"
    print(f"\n{mode}: {total_expired:,} points past TTL")
    return 0


if __name__ == "__main__":
    sys.exit(main())

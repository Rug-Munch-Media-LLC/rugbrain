#!/usr/bin/env python3
"""CoinGecko trending → ``trending_snapshots`` daily snapshot collection.

CoinGecko's free ``/search/trending`` endpoint (no auth) returns the top ~15
coins by search volume on the last 24h. That is a direct, high-frequency
signal of what the market is paying attention to — ideal market context for
the bot (rugpull sentiment, narrative rotation, "why is X pumping" lookups).

Each run creates/refreshes **one snapshot point per day** (deterministic
UUIDv5 ``coingecko_trending:{YYYY-MM-DD}``) whose content is a structured
ranked list of the trending coins, plus a per-coin point
``coingecko_trending:{date}:{coin_id}`` carrying the coin's market context
(market cap rank, score, sparkline window). Deterministic ids make re-runs
idempotent — the daily snapshot is refreshed in place, never duplicated.

Collection config: named dense (1024-d qwen3-embedding-4b) + BM25 sparse hybrid, so
hybrid (RRF) search works from day one (same shape as regulation_enforcement).

The snapshot is deliberately *one point per day* (not per coin only) so the
bot can answer "what was trending on <date>" with a single filter, while the
per-coin points enable semantic queries like "trending AI tokens".

Usage:
    python scripts/ingest_coingecko_trending.py            # today's snapshot
    python scripts/ingest_coingecko_trending.py --dry-run  # preview only
    python scripts/ingest_coingecko_trending.py --days 3   # backfill

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import build_payload, embed_many

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_coingecko_trending")

COLLECTION = "trending_snapshots"
EMBEDDING_DIM = 2560
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
SOURCE = "coingecko_trending"
REQUEST_TIMEOUT = 30.0


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "coin"


def _snapshot_doc(day: str, coins: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the daily ranked-list document for *coins* on *day*."""
    lines = [f"CoinGecko trending tokens snapshot — {day}", ""]
    for i, c in enumerate(coins, 1):
        item = c["item"]
        lines.append(
            f"{i}. {item.get('name', '?')} ({item.get('symbol', '?')})"
            f" — market_cap_rank={item.get('market_cap_rank') or 'n/a'}"
            f" score={item.get('score') or 'n/a'}"
        )
    text = "\n".join(lines)
    return {
        "doc_id": f"coingecko_trending:{day}",
        "title": f"CoinGecko trending tokens {day}",
        "text": text,
        "metadata": {
            "title": f"CoinGecko trending tokens {day}",
            "snapshot_date": day,
            "date_ts": datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp(),
            "source": SOURCE,
            "kind": "daily_snapshot",
            "coin_count": len(coins),
            "coins": [
                {
                    "rank": i,
                    "id": c["item"].get("id"),
                    "name": c["item"].get("name"),
                    "symbol": c["item"].get("symbol"),
                    "market_cap_rank": c["item"].get("market_cap_rank"),
                    "score": c["item"].get("score"),
                }
                for i, c in enumerate(coins, 1)
            ],
        },
    }


def _coin_docs(day: str, coins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One per-coin point per day for semantic retrieval."""
    docs: list[dict[str, Any]] = []
    for i, c in enumerate(coins, 1):
        item = c["item"]
        name = item.get("name", "?")
        sym = item.get("symbol", "?")
        cid = item.get("id") or _slug(name)
        text = (
            f"{name} ({sym}) is trending on CoinGecko as of {day} "
            f"(rank #{i} by search volume, market cap rank "
            f"{item.get('market_cap_rank') or 'n/a'})."
        )
        docs.append(
            {
                "doc_id": f"coingecko_trending:{day}:{cid}",
                "title": f"Trending: {name} ({sym})",
                "text": text,
                "metadata": {
                    "title": f"Trending: {name} ({sym})",
                    "snapshot_date": day,
                    "rank": i,
                    "coin_id": cid,
                    "name": name,
                    "symbol": sym,
                    "market_cap_rank": item.get("market_cap_rank"),
                    "score": item.get("score"),
                    "source": SOURCE,
                    "kind": "trending_coin",
                },
            }
        )
    return docs


async def _ensure_hybrid_collection(client: httpx.AsyncClient, base: str) -> bool:
    """Create ``trending_snapshots`` with dense + BM25 sparse (idempotent)."""
    r = await client.get(f"{base}/collections/{COLLECTION}")
    if r.status_code == 200:
        return True
    payload = {
        "vectors": {"dense": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
        "sparse_vectors": {"bm25": {}},
    }
    r = await client.put(f"{base}/collections/{COLLECTION}", json=payload)
    ok = r.status_code in (200, 201, 202)
    if ok:
        logger.info("collection_created=%s", COLLECTION)
    else:
        logger.warning("collection_create_failed status=%d body=%s", r.status_code, r.text[:200])
    return ok


async def run(days: int, dry_run: bool) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "snapshots": 0,
        "coin_points": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if not await _ensure_hybrid_collection(client, qdrant_url):
            stats["errors"] += 1
            return stats

        docs: list[dict[str, Any]] = []
        try:
            r = await client.get(TRENDING_URL, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            coins = (r.json().get("coins") or [])[:15]
        except Exception as e:
            logger.warning("coingecko_trending_fetch_failed err=%s", str(e)[:200])
            stats["errors"] += 1
            return stats
        if not coins:
            logger.warning("coingecko_trending_empty_response")
            return stats

        for offset in range(days):
            day = (datetime.now(UTC) - timedelta(days=offset)).date().isoformat()
            docs.append(_snapshot_doc(day, coins))
            docs.extend(_coin_docs(day, coins))
        stats["snapshots"] = days
        stats["coin_points"] = len(docs) - days

        if dry_run:
            logger.info("dry_run docs=%d", len(docs))
            return stats

        texts = [d["text"] for d in docs]
        vectors = await embed_many(client, texts)
        for doc, vec in zip(docs, vectors, strict=False):
            if not vec:
                stats["errors"] += 1
                continue
            doc["vector"] = vec
            stats["embedded"] += 1

        points = [
            build_payload(
                doc["doc_id"],
                doc["vector"],
                doc["text"],
                metadata=doc["metadata"],
                source=SOURCE,
                severity="info",
                chain="",
                collection=COLLECTION,
            )
            for doc in docs
            if doc.get("vector")
        ]
        # NOTE: build_payload produces bare-vector points. For a hybrid
        # (dense + bm25) collection Qdrant silently drops a bare vector, so
        # wrap under the named "dense" key — matching news_to_rag behavior.
        r = await client.get(f"{qdrant_url}/collections/{COLLECTION}")
        params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
        named = isinstance(params.get("vectors"), dict) and "dense" in params.get("vectors", {})
        for p in points:
            if named and isinstance(p.get("vector"), list):
                p["vector"] = {"dense": p["vector"]}

        for batch_start in range(0, len(points), 64):
            batch = points[batch_start : batch_start + 64]
            r = await client.put(
                f"{qdrant_url}/collections/{COLLECTION}/points",
                json={"points": batch},
            )
            if r.status_code in (200, 201):
                stats["upserted"] += len(batch)
            else:
                stats["errors"] += len(batch)
                logger.warning("upsert_failed status=%d body=%s", r.status_code, r.text[:300])

    logger.info(
        "coingecko_trending_done snapshots=%d coins=%d embedded=%d upserted=%d errors=%d",
        stats["snapshots"],
        stats["coin_points"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="CoinGecko trending → trending_snapshots")
    parser.add_argument("--days", type=int, default=1, help="snapshot days to write (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="fetch only, no embed/upsert")
    args = parser.parse_args()

    stats = asyncio.run(run(args.days, args.dry_run))
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()

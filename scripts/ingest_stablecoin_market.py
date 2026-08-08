#!/usr/bin/env python3
"""Ingest DeFiLlama stablecoin market-cap data into the RAG ``stablecoin_market`` collection.

Source: https://stablecoins.llama.fi/stablecoins (maintained by the
DeFiLlama team) — 400+ pegged assets with current market cap (circulating
``peggedUSD``), price, peg type, peg mechanism, and per-chain circulation.
This gives the bot's market context a canonical "how big is the stablecoin
ecosystem / where is liquidity" signal that complements the existing news +
threat-intel RAG collections.

Each stablecoin is embedded (1024-dim bge-m3 via Infinity) and upserted
into ``stablecoin_market`` (bare 1024-dim Cosine — created on first run).
A single aggregate document ("stablecoin ecosystem summary") is also stored
with the total market cap and top assets so generic market queries hit a
compact digest.

Idempotent: point IDs are deterministic UUIDv5 (``stablecoin:{symbol}`` /
``stablecoin:summary``), so re-running overwrites the same points instead
of duplicating.

Cron usage (fresh-only, matches the nightly maintenance cadence):

    python3 scripts/ingest_stablecoin_market.py

Usage:
    python scripts/ingest_stablecoin_market.py --dry-run --limit 10
    python scripts/ingest_stablecoin_market.py            # full 400+ assets

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import embed_many

from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_stablecoin_market")

STABLECOINS_API = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
COLLECTION = "stablecoin_market"
SOURCE = "defillama_stablecoins"
SOURCE_URL = "https://stablecoins.llama.fi"
EMBEDDING_DIM = 1024
REQUEST_TIMEOUT = 60.0
SUMMARY_TOP = 15  # top assets included in the aggregate summary doc


def _fmt_usd(value: float | None) -> str:
    """Format a USD amount for the summary doc ($2.3T / $183.1B / $72.3M)."""
    if not value:
        return "unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


def _pct_change(prev: Any, cur: Any) -> float | None:
    """Percent change between two USD quantities; None when undefined."""
    try:
        p = float(prev or 0)
        c = float(cur or 0)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return round((c - p) / p * 100.0, 2)


async def fetch_stablecoins(client: httpx.AsyncClient, limit: int | None) -> list[dict[str, Any]]:
    r = await client.get(STABLECOINS_API, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not isinstance(data.get("peggedAssets"), list):
        logger.warning("stablecoins_unexpected_payload type=%s", type(data).__name__)
        return []
    assets = data["peggedAssets"]
    if limit:
        assets = assets[:limit]
    logger.info("stablecoins_fetched total=%d", len(assets))
    return assets


def build_docs(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw stablecoin assets into RAG docs.

    Returns a list of ``{doc_id, title, text, metadata}`` dicts plus the
    aggregate summary doc. ``metadata`` is filterable in Qdrant (source,
    symbol, peg_type, mcap_usd, date).
    """
    docs: list[dict[str, Any]] = []
    now_iso = datetime.now(UTC).date().isoformat()
    total_mcap = 0.0
    top_assets: list[dict[str, Any]] = []

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        symbol = str(asset.get("symbol") or "").strip().upper()
        name = str(asset.get("name") or "").strip() or symbol or "unknown"
        mcap = float(asset.get("circulating", {}).get("peggedUSD") or 0)
        price = asset.get("price")
        peg_type = str(asset.get("pegType") or "").strip()
        mechanism = str(asset.get("pegMechanism") or "").strip()
        prev_day = asset.get("circulatingPrevDay", {}).get("peggedUSD")
        prev_week = asset.get("circulatingPrevWeek", {}).get("peggedUSD")
        prev_month = asset.get("circulatingPrevMonth", {}).get("peggedUSD")
        chains = list((asset.get("chainCirculating") or {}).keys())[:12]

        total_mcap += mcap
        if symbol and mcap > 0:
            top_assets.append({"symbol": symbol, "name": name, "mcap": mcap, "price": price})

        text = (
            f"Title: Stablecoin {name} ({symbol})\n"
            f"Symbol: {symbol}\n"
            f"Market cap: {_fmt_usd(mcap)}\n"
            f"Price: {price if price is not None else 'unknown'} USD\n"
            f"Peg type: {peg_type or 'unknown'}\n"
            f"Peg mechanism: {mechanism or 'unknown'}\n"
            f"Chains: {', '.join(chains) or 'unknown'}\n"
            f"24h change: {_pct_change(prev_day, mcap) if prev_day else 'unknown'}%\n"
            f"7d change: {_pct_change(prev_week, mcap) if prev_week else 'unknown'}%\n"
            f"30d change: {_pct_change(prev_month, mcap) if prev_month else 'unknown'}%"
        )
        doc_id = f"stablecoin:{symbol or name.lower()}"
        docs.append(
            {
                "doc_id": doc_id,
                "title": f"Stablecoin {name} ({symbol})",
                "text": text,
                "metadata": {
                    "title": f"Stablecoin {name} ({symbol})",
                    "symbol": symbol or None,
                    "name": name,
                    "mcap_usd": round(mcap, 2) if mcap else None,
                    "price_usd": price,
                    "peg_type": peg_type or None,
                    "peg_mechanism": mechanism or None,
                    "chains": chains,
                    "date": now_iso,
                    "source_type": SOURCE,
                    "source_url": SOURCE_URL,
                },
            }
        )

    # Aggregate ecosystem summary doc — generic market queries hit this.
    top_assets_sorted = sorted(top_assets, key=lambda a: a["mcap"], reverse=True)[:SUMMARY_TOP]
    top_lines = "\n".join(
        f"  {a['symbol']} ({a['name']}): {_fmt_usd(a['mcap'])}" for a in top_assets_sorted
    )
    summary_text = (
        f"Title: Stablecoin ecosystem summary\n"
        f"Total stablecoin market cap: {_fmt_usd(total_mcap)}\n"
        f"Top {len(top_assets_sorted)} stablecoins by market cap:\n{top_lines}"
    )
    docs.append(
        {
            "doc_id": "stablecoin:summary",
            "title": "Stablecoin ecosystem summary",
            "text": summary_text,
            "metadata": {
                "title": "Stablecoin ecosystem summary",
                "symbol": None,
                "name": "Stablecoin ecosystem summary",
                "mcap_usd": round(total_mcap, 2) if total_mcap else None,
                "total_stablecoin_mcap_usd": round(total_mcap, 2) if total_mcap else None,
                "top_assets": [a["symbol"] for a in top_assets_sorted],
                "date": now_iso,
                "source_type": SOURCE,
                "source_url": SOURCE_URL,
            },
        }
    )
    logger.info(
        "stablecoins_docs total=%d summary_mcap=%s",
        len(docs),
        _fmt_usd(total_mcap),
    )
    return docs


async def run(dry_run: bool, limit: int | None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "source": SOURCE,
        "fetched": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            assets = await fetch_stablecoins(client, limit)
        except Exception as e:
            logger.warning("stablecoins_fetch_failed: %s", str(e)[:200])
            stats["errors"] += 1
            return stats
        stats["fetched"] = len(assets)

        docs = build_docs(assets)
        if not docs:
            logger.info("stablecoins_no_docs_to_ingest")
            return stats

        texts = [d["text"] for d in docs]
        vectors = await embed_many(client, texts)
        for doc, vec in zip(docs, vectors, strict=False):
            if not vec:
                stats["errors"] += 1
                continue
            doc["vector"] = vec
            stats["embedded"] += 1

        if dry_run:
            logger.info(
                "dry_run would_upsert=%d into=%s",
                stats["embedded"],
                COLLECTION,
            )
            return stats

        # QdrantVectorStore lazily creates its own httpx client on first
        # use; insert() creates the bare-vector collection when missing.
        store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://localhost:6333"))
        for doc in docs:
            vec = doc.get("vector")
            if not vec:
                continue
            ok = await store.insert(
                doc_id=doc["doc_id"],
                collection=COLLECTION,
                embedding=vec,
                content=doc["text"],
                metadata=doc["metadata"],
                source=SOURCE,
                severity="low",  # market data — informational
                chain="multi",
            )
            if ok:
                stats["upserted"] += 1
            else:
                stats["errors"] += 1

    logger.info(
        "stablecoins_done fetched=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["fetched"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="DeFiLlama stablecoins → stablecoin_market")
    parser.add_argument("--dry-run", action="store_true", help="fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="max stablecoins to ingest (cap)")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit))
    print(json_dumps(stats))


def json_dumps(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, indent=2)


if __name__ == "__main__":
    main()

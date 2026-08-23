#!/usr/bin/env python3
"""Ingest the DeFiLlama hacks database into the RAG ``threat_intel_v2`` collection.

Source: https://api.llama.fi/hacks (maintained by the DeFiLlama team) —
600+ structured exploit incidents with date, USD amount lost, chain(s),
technique, classification, target type and returned-funds flag. This is
the fresh replacement for the dead Rekt News RSS feed (https://rekt.news/feed/
returns HTTP 500 and the RSS paths are gone, so RSS-based consumers like
``historical.scrape_rekt_news`` are unavailable; the leaderboard
``__NEXT_DATA__`` path in ``ingest_scam_knowledge.py`` still works).
DeFiLlama covers the same incident universe with a maintained, structured
API.

Each hack is embedded (2560-dim qwen3-embedding-4b via ) and upserted into
``threat_intel_v2`` (dense + BM25 sparse hybrid — the REAL collection;
the legacy ``threat_intel`` name was migrated away and no alias exists, so
writing to it would create an empty fragment) with canonical filterable
metadata: date, date_ts, amount_lost_usd, chain, attack_vector, technique,
classification, source_type=defillama_hacks.

Idempotent: point IDs are deterministic seeds (``defillama_hacks:{name}:{date_ts}``)
which the store converts to UUIDv5 point IDs, so re-running overwrites the
same points instead of duplicating.

Cron usage (fresh-only increment, matches the every-6h intel cadence):

    python3 scripts/ingest_defillama_hacks.py --days 90

Usage:
    python scripts/ingest_defillama_hacks.py --dry-run --limit 10
    python scripts/ingest_defillama_hacks.py --days 90
    python scripts/ingest_defillama_hacks.py            # full 600+ history

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import embed_many

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_defillama_hacks")

DEFILLAMA_HACKS_API = "https://api.llama.fi/hacks"
COLLECTION = "threat_intel_v2"  # real hybrid collection (dense + BM25 sparse)
SOURCE = "defillama_hacks"
SOURCE_URL = "https://defillama.com/hacks"
EMBEDDING_DIM = 2560
REQUEST_TIMEOUT = 60.0

# keyword → canonical attack vector (mirrors ingest_scam_knowledge._CONTENT_VECTORS)
_CONTENT_VECTORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"reentranc", re.I), "reentrancy"),
    (re.compile(r"flash\s?loan", re.I), "flash-loan"),
    (re.compile(r"oracle", re.I), "oracle-manipulation"),
    (re.compile(r"price\s?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access\s?control", re.I), "access-control"),
    (re.compile(r"onlyowner|admin\s?privilege", re.I), "access-control"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"rug\s?pull|rugpull", re.I), "rug-pull"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"private\s?key|seed\s?phrase", re.I), "key-compromise"),
    (re.compile(r"withdraw", re.I), "withdrawal-exploit"),
    (re.compile(r"denial\s?of\s?service|dos\b", re.I), "denial-of-service"),
    (re.compile(r"front\s?run", re.I), "front-running"),
    (re.compile(r"supply\s?chain", re.I), "supply-chain"),
    (re.compile(r"input\s?valid", re.I), "input-validation"),
]


def _detect_attack_vector(text: str) -> str:
    for pattern, vector in _CONTENT_VECTORS:
        if pattern.search(text or ""):
            return vector
    return "unknown"


def _severity_for_amount(amount: float | None) -> str:
    """Severity from USD amount lost (matches existing knowledge ingests)."""
    if not amount:
        return "medium"
    if amount >= 10_000_000:
        return "critical"
    if amount >= 1_000_000:
        return "high"
    return "medium"


async def fetch_hacks(client: httpx.AsyncClient, limit: int | None) -> list[Any]:
    r = await client.get(DEFILLAMA_HACKS_API, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    hacks = r.json() or []
    if not isinstance(hacks, list):
        logger.warning("defillama_unexpected_payload type=%s", type(hacks).__name__)
        return []
    if limit:
        hacks = hacks[:limit]
    logger.info("defillama_hacks_fetched total=%d", len(hacks))
    return hacks


def build_docs(hacks: list[Any], days: int | None) -> list[dict[str, Any]]:
    """Normalize raw DeFiLlama hacks into RAG docs.

    Returns a list of ``{doc_id, title, text, metadata}`` dicts. When *days*
    is set, only hacks dated within the last *days* are kept (cron
    incremental mode).
    """
    cutoff_ts: float | None = None
    if days:
        cutoff_ts = (datetime.now(UTC) - timedelta(days=days)).timestamp()

    docs: list[dict[str, Any]] = []
    skipped_recent = 0
    for hack in hacks:
        if not isinstance(hack, dict):
            continue
        ts = float(hack.get("date") or 0)
        date_iso = datetime.fromtimestamp(ts, tz=UTC).date().isoformat() if ts > 0 else ""
        if cutoff_ts is not None and ts < cutoff_ts:
            skipped_recent += 1
            continue
        name = str(hack.get("name") or "").strip() or "unknown"
        amount = float(hack.get("amount") or 0) or None
        chains = hack.get("chain") or []
        chain = str(chains[0]).lower() if isinstance(chains, list) and chains else "EVM"
        technique = str(hack.get("technique") or "").strip()
        classification = str(hack.get("classification") or "").strip()
        target_type = str(hack.get("targetType") or "").strip()
        language = str(hack.get("language") or "").strip()
        returned = hack.get("returnedFunds")
        bridge_hack = bool(hack.get("bridgeHack"))
        vector = _detect_attack_vector(f"{name} {technique} {classification}")

        text = (
            f"Title: {name}\n"
            f"Date: {date_iso or 'unknown'}\n"
            f"Funds lost: {amount if amount else 'unknown'}\n"
            f"Technique: {technique or 'unknown'}\n"
            f"Classification: {classification or 'unknown'}\n"
            f"Chain: {chain}\n"
            f"Language: {language or 'unknown'}"
        )
        doc_id = f"defillama_hacks:{name.lower()}:{date_iso or (ts if ts > 0 else 'unknown')}"
        docs.append(
            {
                "doc_id": doc_id,
                "title": f"DeFi hack: {name}",
                "text": text,
                "metadata": {
                    "title": f"DeFi hack: {name}",
                    "protocol": name,
                    "date": date_iso,
                    "date_ts": ts if ts > 0 else None,
                    "amount_lost_usd": amount,
                    "chain": chain,
                    "attack_vector": vector,
                    "technique": technique or None,
                    "classification": classification or None,
                    "target_type": target_type or None,
                    "language": language or None,
                    "returned_funds": returned,
                    "bridge_hack": bridge_hack,
                    "source_type": SOURCE,
                    "source_url": SOURCE_URL,
                },
            }
        )
    logger.info(
        "defillama_docs total=%d recent_skipped=%d",
        len(docs),
        skipped_recent,
    )
    return docs


async def run(dry_run: bool, limit: int | None, days: int | None) -> dict[str, Any]:
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
            hacks = await fetch_hacks(client, limit)
        except Exception as e:
            logger.warning("defillama_fetch_failed: %s", str(e)[:200])
            stats["errors"] += 1
            return stats
        stats["fetched"] = len(hacks)

        docs = build_docs(hacks, days)
        if not docs:
            logger.info("defillama_no_docs_to_ingest")
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
        # use; insert() probes the collection config and writes named dense
        # + BM25 sparse automatically (hybrid threat_intel_v2).
        store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
        for doc in docs:
            vec_raw: Any = doc.get("vector")
            if not vec_raw:
                continue
            vec_f: list[float] = [float(x) for x in vec_raw]
            ok = await store.insert(
                doc_id=doc["doc_id"],
                collection=COLLECTION,
                embedding=vec_f,
                content=doc["text"],
                metadata=doc["metadata"],
                source=SOURCE,
                severity=_severity_for_amount(doc["metadata"].get("amount_lost_usd")),
                chain=doc["metadata"].get("chain") or "",
            )
            if ok:
                stats["upserted"] += 1
            else:
                stats["errors"] += 1

    logger.info(
        "defillama_done fetched=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["fetched"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="DeFiLlama hacks → threat_intel")
    parser.add_argument("--dry-run", action="store_true", help="fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="max hacks to ingest (cap)")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only hacks within the last N days (cron incremental mode)",
    )
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit, args.days))
    print(json_dumps(stats))


def json_dumps(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, indent=2)


if __name__ == "__main__":
    main()

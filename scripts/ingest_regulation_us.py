#!/usr/bin/env python3
"""Ingest US federal crypto enforcement actions into the RAG regulation_enforcement_8b collection.

Sources (all public, official, no auth):
  1. CFTC Enforcement RSS — https://www.cftc.gov/RSS/RSSENF/rssenf.xml
     (rolling feed of CFTC enforcement actions; crypto-filtered)

NOT WORKING / BLOCKED:
  - FTC Press Releases RSS — blocks automated requests (403)
  - FinCEN — no public RSS feed found
  - DOJ — RSS feed at justice.gov/rss.xml exists but contains old/general content, not enforcement-specific

These complement the existing SEC RSS feeds in ingest_regulation_enforcement.py
to provide comprehensive US federal enforcement coverage.

Each item is embedded (4096-dim qwen3-embedding-8b) and upserted into
regulation_enforcement_8b with filterable metadata: date, date_ts, agency, action_type,
source_type, case_url, amount_usd (when available), severity.

Idempotent: point IDs are deterministic UUIDv5
(f"regulation:{agency}:{slug}:{date_iso}"), so re-running overwrites the same points.

Usage:
    python scripts/ingest_regulation_us.py --dry-run
    python scripts/ingest_regulation_us.py --days 90
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from defusedxml import ElementTree as element_tree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import embed_many
from ingest_common2 import build_doc, write_docs

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_regulation_us")

COLLECTION = "regulation_enforcement_8b"
REQUEST_TIMEOUT = 60.0
USER_AGENT = "RugMunch RAG research agent; admin@rugmunch.io"

# Source RSS feeds - only working ones
CFTC_ENFORCEMENT_RSS = "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"

# Crypto enforcement keywords - broad enough to catch relevant items
_CRYPTO_KW = [
    r"cryptocurrenc",
    r"crypto asset",
    r"crypto-asset",
    r"digital asset",
    r"blockchain",
    r"bitcoin",
    r"\beth(?:er|ereum)\b",
    r"tether",
    r"stablecoin",
    r"coinbase",
    r"binance",
    r"kraken",
    r"ripple",
    r"\bxrp\b",
    r"terraform",
    r"\bluna\b",
    r"celsius",
    r"blockfi",
    r"genesis global",
    r"\bftx\b",
    r"bankman-fried",
    r"do kwon",
    r"initial coin offering",
    r"\bico\b",
    r"\bnft\b",
    r"\bdefi\b",
    r"unregistered",
    r"crypto lending",
    r"crypto exchange",
    r"crypto firm",
    r"digital token",
    r"crypto wallet",
    r"crypto securities",
    r"wash trading",
    r"pump-and-dump|pump and dump",
    r"mango markets",
    r"tornado cash",
    r"crypto pyramid",
    r"crypto ponzi",
    r"money transmitter",
    r"money laundering",
    r"sanctions",
    r"ofac",
    r"bfi",
    r"bfi.*crypto",
    r"virtual currency",
]
_CRYPTO_RE = re.compile("|".join(_CRYPTO_KW), re.I)


def _is_crypto_item(title: str, description: str) -> bool:
    """Check if an RSS item is crypto-related."""
    haystack = " ".join([title, description])
    return bool(_CRYPTO_RE.search(haystack))


async def _lenient_rss_fetch(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """Fetch an RSS feed and parse items even when the XML is malformed."""
    r = await client.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    raw = r.text
    fixed = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&", raw)
    items: list[dict[str, Any]] = []
    try:
        root = element_tree.fromstring(fixed)
        for it in root.findall(".//item"):
            items.append(
                {
                    "title": html_lib.unescape(it.findtext("title") or "").strip(),
                    "link": (it.findtext("link") or "").strip(),
                    "description": html_lib.unescape(it.findtext("description") or "").strip(),
                    "pubDate": (it.findtext("pubDate") or "").strip(),
                }
            )
    except element_tree.ParseError:
        for block in re.split(r"<item>", fixed)[1:]:
            item: dict[str, Any] = {
                "title": "",
                "link": "",
                "description": "",
                "pubDate": "",
            }
            m = re.search(r"<title>(.*?)</title>", block, re.S)
            if m:
                item["title"] = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            m = re.search(r"<link>(.*?)</link>", block, re.S)
            if m:
                item["link"] = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            m = re.search(r"<description>(.*?)</description>", block, re.S)
            if m:
                item["description"] = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            if m:
                item["pubDate"] = html_lib.unescape(m.group(1)).strip()
            if item["title"] or item["link"]:
                items.append(item)
    logger.info("rss_items url=%s total=%d", url.split("/")[2], len(items))
    return items


def _rss_docs(items: list[dict[str, Any]], agency: str, days: int | None) -> list[dict[str, Any]]:
    """Filter RSS items for crypto enforcement and normalize to RAG docs."""
    docs: list[dict[str, Any]] = []
    cutoff: datetime | None = None
    if days:
        cutoff = datetime.now(UTC) - timedelta(days=days)
    for it in items:
        title = it["title"]
        if not title:
            continue
        if not _is_crypto_item(title, it["description"]):
            continue
        pub_ts = 0.0
        if it["pubDate"]:
            try:
                pub_ts = parsedate_to_datetime(it["pubDate"]).timestamp()
            except (ValueError, TypeError):
                pub_ts = 0.0
        if cutoff is not None and pub_ts and pub_ts < cutoff.timestamp():
            continue
        date_iso = datetime.fromtimestamp(pub_ts, tz=UTC).date().isoformat() if pub_ts else ""
        text = (
            f"Title: {title}\n"
            f"Agency: {agency}\n"
            f"Date: {date_iso or 'unknown'}\n"
            f"Summary: {it['description'] or 'none'}"
        )
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "untitled"
        docs.append(
            {
                "doc_id": f"regulation:{agency.lower()}:{slug}:{date_iso or int(pub_ts)}",
                "title": title,
                "text": text,
                "metadata": {
                    "title": title,
                    "date": date_iso,
                    "date_ts": pub_ts if pub_ts else None,
                    "agency": agency,
                    "action_type": "enforcement_action",
                    "source_type": f"{agency.lower()}_enforcement_rss",
                    "case_url": it["link"],
                    "amount_usd": None,
                    "severity": "high",
                },
            }
        )
    return docs


async def run(
    dry_run: bool,
    days: int | None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "cftc_items": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            docs: list[dict[str, Any]] = []

            # Fetch from CFTC enforcement RSS
            try:
                items = await _lenient_rss_fetch(client, CFTC_ENFORCEMENT_RSS)
                agency_docs = _rss_docs(items, "CFTC", days)
                docs.extend(agency_docs)
                stats["cftc_items"] = len(agency_docs)
            except Exception as e:
                logger.warning(
                    "rss_fetch_failed url=%s agency=CFTC err=%s", CFTC_ENFORCEMENT_RSS, str(e)[:150]
                )

            stats["feed_items_crypto"] = len(docs)
        except Exception as e:
            logger.warning("regulation_us_fetch_error: %s", str(e)[:200])
            stats["errors"] += 1
            return stats

        if not docs:
            logger.info("regulation_us_no_docs_to_ingest")
            return stats

        # Build docs using ingest_common2 pattern
        built_docs = []
        for doc in docs:
            built_docs.append(
                build_doc(
                    source="regulation_us_ingest",
                    rel_key=doc["doc_id"],
                    content=doc["text"],
                    metadata=doc["metadata"],
                    severity="high",
                    collection=COLLECTION,
                )
            )

        if dry_run:
            logger.info("dry_run would_upsert=%d into=%s", len(built_docs), COLLECTION)
            return stats

        # Embed and upsert using write_docs from ingest_common2
        store = QdrantVectorStore()
        result = await write_docs(
            client,
            store,
            built_docs,
            source="regulation_us_ingest",
            collection=COLLECTION,
            dry_run=False,
            chunk_size=2000,
            chunk_overlap=100,
        )

        stats["embedded"] = len(built_docs)
        stats["upserted"] = result.get("inserted", 0)
        stats["errors"] = result.get("failed", 0)

    logger.info(
        "regulation_us_done cftc=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["cftc_items"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="US federal enforcement → Qdrant")
    parser.add_argument("--dry-run", action="store_true", help="fetch + embed but do not upsert")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only RSS items within the last N days (cron incremental mode)",
    )
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.days))
    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

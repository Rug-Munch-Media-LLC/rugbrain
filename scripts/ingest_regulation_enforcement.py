#!/usr/bin/env python3
"""Ingest crypto regulatory / law-enforcement actions into the RAG
``regulation_enforcement`` collection.

SEC enforcement and DOJ/CFTC crypto actions are rare but high-signal for
the bot's risk analysis: a token's exchange, founder, or chain being sued
or charged is a material risk event. This script keeps a curated
collection of these actions fresh from official feeds plus a landmark seed.

Sources (all public, official, no auth):
  1. SEC Litigation Releases RSS — https://www.sec.gov/enforcement-litigation/litigation-releases/rss
     (rolling ~25-item feed of SEC enforcement lawsuits; crypto-filtered)
  2. SEC Press Releases RSS       — https://www.sec.gov/news/pressreleases.rss
     (rolling ~25-item feed; crypto-filtered for 'SEC Charges...' style items)
  3. Landmark seed — ~15 major SEC / DOJ / CFTC crypto enforcement actions
     (2020-2024) hardcoded below so the collection is useful from day one
     while the rolling feeds accumulate new actions.

The SEC RSS feeds are occasionally malformed XML (unescaped ``&`` in
titles) — this script parses them leniently.

Each item is embedded (1024-dim bge-m3 via Infinity) and upserted into
``regulation_enforcement`` (dense + BM25 sparse hybrid, created on first
run) with filterable metadata: date, date_ts, agency, action_type,
source_type, case_url, amount_usd, severity.

Idempotent: point IDs are deterministic UUIDv5
(``regulation:{agency}:{slug}:{date_iso}``), so re-running overwrites the
same points instead of duplicating.

Cron usage (every 6h, matches the intel enrichment cadence):

    python3 scripts/ingest_regulation_enforcement.py --days 90

Usage:
    python scripts/ingest_regulation_enforcement.py --dry-run
    python scripts/ingest_regulation_enforcement.py --days 90
    python scripts/ingest_regulation_enforcement.py --seed-only

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import html as html_lib
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from defusedxml import ElementTree as element_tree  # noqa: N813 (safe parse of untrusted RSS)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import embed_many

from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_regulation_enforcement")

COLLECTION = "regulation_enforcement"
EMBEDDING_DIM = 1024
REQUEST_TIMEOUT = 60.0
USER_AGENT = "RugMunch RAG research agent; admin@rugmunch.io"

SEC_LITIGATION_RSS = "https://www.sec.gov/enforcement-litigation/litigation-releases/rss"
SEC_PRESS_RSS = "https://www.sec.gov/news/pressreleases.rss"

# Strict crypto/cyber enforcement vocabulary. Bare 'token'/'coin'/'fraud'
# are too broad for SEC titles (they appear in non-crypto releases), so we
# require these more specific signals.
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
]
_CRYPTO_RE = re.compile("|".join(_CRYPTO_KW), re.I)


def _is_crypto_item(title: str, description: str, categories: list[str]) -> bool:
    haystack = " ".join([title, description, " ".join(categories)])
    return bool(_CRYPTO_RE.search(haystack))


def _lenient_rss_fetch(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """Fetch an RSS feed and parse items even when the XML is malformed.

    SEC feeds have shipped unescaped ``&`` inside titles (e.g. 'DJ&S
    Property'). We pre-escape bare ampersands, then fall back to a
    regex-based item extractor if ElementTree still fails.
    """
    r = client.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    raw = r.text
    fixed = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", raw)
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
                    "categories": [
                        html_lib.unescape(c.text or "").strip()
                        for c in it.findall("category")
                        if c.text
                    ],
                }
            )
    except element_tree.ParseError:
        # Last-resort: manual item splitting (SEC feed has item-level breaks).
        for block in re.split(r"<item>", fixed)[1:]:
            item: dict[str, Any] = {
                "title": "",
                "link": "",
                "description": "",
                "pubDate": "",
                "categories": [],
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


def _rss_docs(
    items: list[dict[str, Any]], source_type: str, days: int | None
) -> list[dict[str, Any]]:
    """Filter RSS items for crypto enforcement and normalize to RAG docs."""
    docs: list[dict[str, Any]] = []
    cutoff: datetime | None = None
    if days:
        cutoff = datetime.now(UTC) - timedelta(days=days)
    for it in items:
        title = it["title"]
        if not title:
            continue
        if not _is_crypto_item(title, it["description"], it["categories"]):
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
            f"Title: {title}\nDate: {date_iso or 'unknown'}\nSummary: {it['description'] or 'none'}"
        )
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "untitled"
        docs.append(
            {
                "doc_id": f"regulation:{source_type}:{slug}:{date_iso or int(pub_ts)}",
                "title": title,
                "text": text,
                "metadata": {
                    "title": title,
                    "date": date_iso,
                    "date_ts": pub_ts if pub_ts else None,
                    "agency": "SEC",
                    "action_type": (
                        "litigation_release"
                        if source_type == "sec_litigation_rss"
                        else "press_release"
                    ),
                    "source_type": source_type,
                    "case_url": it["link"],
                    "amount_usd": None,
                },
            }
        )
    return docs


# ── Landmark seed (SEC / DOJ / CFTC crypto enforcement, 2020-2024) ─────
# Factual summaries of major public actions. Rolling RSS feeds keep the
# collection fresh; these give the bot the key precedents from day one.
_SEED_CASES: list[dict[str, Any]] = [
    {
        "date": "2020-12-22",
        "agency": "SEC",
        "title": "SEC v. Ripple Labs (XRP)",
        "summary": (
            "SEC sued Ripple Labs and two executives for conducting a $1.3B "
            "unregistered securities offering of XRP. July 2023: court ruled "
            "programmatic XRP sales on exchanges were not securities; SEC "
            "settled in 2025 with Ripple paying $50M."
        ),
        "amount_usd": 1300000000,
        "url": "https://www.sec.gov/news/press-releases/2020-338",
    },
    {
        "date": "2022-02-08",
        "agency": "DOJ",
        "title": "Bitfinex hack money laundering (Lichtenstein & Morgan)",
        "summary": (
            "DOJ arrested Ilya Lichtenstein and Heather Morgan for laundering "
            "119,754 BTC (~$3.6B at seizure) stolen in the 2016 Bitfinex hack. "
            "Lichtenstein sentenced to 5 years in 2024."
        ),
        "amount_usd": 3600000000,
        "url": "https://www.justice.gov/opa/pr/two-arrested-3-6-billion-cryptocurrency-laundering-scheme",
    },
    {
        "date": "2022-02-14",
        "agency": "SEC",
        "title": "SEC v. BlockFi (crypto lending)",
        "summary": (
            "SEC settled with BlockFi for $100M over unregistered retail crypto "
            "lending product BlockFi Interest Accounts — first crypto lending "
            "settlement of its kind."
        ),
        "amount_usd": 100000000,
        "url": "https://www.sec.gov/news/press-releases/2022-26",
    },
    {
        "date": "2022-11-08",
        "agency": "SEC",
        "title": "SEC charges FTX entities and Sam Bankman-Fried",
        "summary": (
            "FTX collapsed in November 2022. SEC charged FTX, Alameda and "
            "Sam Bankman-Fried with fraud; DOJ convicted SBF in November 2023 "
            "(sentence: 25 years, March 2024). FTX distribution to creditors "
            "completed 2025."
        ),
        "amount_usd": 8000000000,
        "url": "https://www.sec.gov/news/press-releases/2022-201",
    },
    {
        "date": "2023-01-18",
        "agency": "SEC",
        "title": "SEC v. Genesis and Gemini (crypto lending)",
        "summary": (
            "SEC charged Genesis Global Capital and Gemini for offering "
            "unregistered securities through the Gemini Earn lending program; "
            "Genesis later paid a $21M penalty in a separate SEC settlement "
            "over unregistered broker activity."
        ),
        "amount_usd": 21000000,
        "url": "https://www.sec.gov/news/press-releases/2023-7",
    },
    {
        "date": "2023-01-27",
        "agency": "DOJ",
        "title": "Mango Markets exploit (Avraham Eisenberg)",
        "summary": (
            "DOJ charged Avraham Eisenberg with commodities fraud and "
            "manipulation for the ~$110M October 2022 Mango Markets exploit; "
            "convicted in April 2024."
        ),
        "amount_usd": 110000000,
        "url": "https://www.justice.gov/usao-sdny/pr/manhattan-federal-jury-convicts-cryptocurrency-trader-mango-markets-manipulation",
    },
    {
        "date": "2023-02-09",
        "agency": "SEC",
        "title": "SEC v. Kraken (staking)",
        "summary": (
            "SEC settled with Kraken over its crypto asset staking-as-a-service "
            "program, paying $30M and ending the program — first staking "
            "settlement."
        ),
        "amount_usd": 30000000,
        "url": "https://www.sec.gov/news/press-releases/2023-25",
    },
    {
        "date": "2023-02-16",
        "agency": "SEC",
        "title": "SEC v. Terraform Labs and Do Kwon",
        "summary": (
            "SEC charged Terraform Labs and Do Kwon with orchestrating a "
            "multi-billion dollar fraud around UST/LUNA (collapse May 2022). "
            "June 2024: $4.47B judgment; Do Kwon extradited to the US in "
            "December 2024."
        ),
        "amount_usd": 4470000000,
        "url": "https://www.sec.gov/news/press-releases/2023-32",
    },
    {
        "date": "2023-03-27",
        "agency": "CFTC",
        "title": "CFTC v. Binance",
        "summary": (
            "CFTC sued Binance and CEO Changpeng Zhao for willful evasion of "
            "US commodities law and operating an unregistered derivatives "
            "exchange. Settled 2023: Binance paid $2.7B to CFTC."
        ),
        "amount_usd": 2700000000,
        "url": "https://www.cftc.gov/PressRoom/PressReleases/8840-23",
    },
    {
        "date": "2023-06-05",
        "agency": "SEC",
        "title": "SEC v. Binance and Changpeng Zhao",
        "summary": (
            "SEC sued Binance, Binance.US and CZ for unregistered exchange, "
            "broker and clearing operations and misstatement of trading "
            "controls. Nov 2023: Binance paid $4.3B (DOJ/FinCEN/OFAC combined) "
            "and CZ stepped down; CZ sentenced to 4 months in April 2024."
        ),
        "amount_usd": 4300000000,
        "url": "https://www.sec.gov/news/press-releases/2023-101",
    },
    {
        "date": "2023-06-06",
        "agency": "SEC",
        "title": "SEC v. Coinbase",
        "summary": (
            "SEC sued Coinbase for operating as an unregistered exchange, "
            "broker and clearing agency and for unregistered offer/sale of "
            "crypto securities. Dismissed by the court in February 2025; "
            "SEC later voted to end its appeal."
        ),
        "amount_usd": None,
        "url": "https://www.sec.gov/news/press-releases/2023-102",
    },
    {
        "date": "2023-07-13",
        "agency": "SEC",
        "title": "SEC v. Celsius and Alex Mashinsky",
        "summary": (
            "SEC charged Celsius Network and former CEO Alex Mashinsky with "
            "defrauding investors via unregistered crypto lending; Mashinsky "
            "pleaded guilty in December 2024 to commodities and wire fraud "
            "related to a ~$4.7B scheme."
        ),
        "amount_usd": 4700000000,
        "url": "https://www.sec.gov/news/press-releases/2023-130",
    },
    {
        "date": "2023-08-23",
        "agency": "DOJ",
        "title": "Tornado Cash sanctions (Roman Storm indictment)",
        "summary": (
            "DOJ indicted Tornado Cash developers Roman Storm and Roman "
            "Semenov for conspiracy to launder money and sanctions evasion "
            "linked to the mixer's role in laundering stolen crypto."
        ),
        "amount_usd": None,
        "url": "https://www.justice.gov/usao-sdny/pr/two-indicted-international-cryptocurrency-mixer-laundered-over-1-billion",
    },
    {
        "date": "2023-08-24",
        "agency": "SEC",
        "title": "SEC v. BarnBridge DAO",
        "summary": (
            "First-ever SEC settlement with a DAO: BarnBridge DAO and founders "
            "paid $1.7M to settle charges over unregistered offers of structured "
            "crypto products (SMART Yield bonds)."
        ),
        "amount_usd": 1700000,
        "url": "https://www.sec.gov/news/press-releases/2023-165",
    },
    {
        "date": "2023-11-21",
        "agency": "SEC",
        "title": "SEC settles with crypto lender Nexo",
        "summary": (
            "Nexo agreed to pay $45M to settle SEC and state charges over "
            "unregistered Earn Interest Product, and ceased offering it in "
            "the US."
        ),
        "amount_usd": 45000000,
        "url": "https://www.sec.gov/news/press-releases/2023-222",
    },
    {
        "date": "2024-04-12",
        "agency": "SEC",
        "title": "SEC v. Consensys / MetaMask (and drop of enforcement)",
        "summary": (
            "SEC sent a Wells notice to Consensys over MetaMask staking and "
            "broker activity and sued in June 2024; the SEC dropped the "
            "enforcement case in February 2025."
        ),
        "amount_usd": None,
        "url": "https://www.sec.gov/newsroom/press-releases/2024-77",
    },
]


def _seed_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for case in _SEED_CASES:
        slug = re.sub(r"[^a-z0-9]+", "-", case["title"].lower()).strip("-")[:80]
        text = (
            f"Title: {case['title']}\n"
            f"Agency: {case['agency']}\n"
            f"Date: {case['date']}\n"
            f"Summary: {case['summary']}"
        )
        ts = 0.0
        with contextlib.suppress(ValueError):
            ts = datetime.strptime(case["date"], "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        docs.append(
            {
                "doc_id": f"regulation:{case['agency'].lower()}:seed:{slug}:{case['date']}",
                "title": case["title"],
                "text": text,
                "metadata": {
                    "title": case["title"],
                    "date": case["date"],
                    "date_ts": ts,
                    "agency": case["agency"],
                    "action_type": "seed_landmark",
                    "source_type": "seed_landmark",
                    "case_url": case["url"],
                    "amount_usd": case["amount_usd"],
                },
            }
        )
    return docs


async def _ensure_hybrid_collection(client: httpx.AsyncClient, base: str) -> bool:
    """Create ``regulation_enforcement`` with named dense + BM25 sparse
    config so RRF hybrid search works from day one (idempotent)."""
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
        logger.info("regulation_collection_created=%s", COLLECTION)
    else:
        logger.warning("regulation_collection_create_failed status=%d", r.status_code)
    return ok


async def run(
    dry_run: bool,
    days: int | None,
    seed_only: bool,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "feed_items_crypto": 0,
        "seed_docs": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if not seed_only and not await _ensure_hybrid_collection(client, qdrant_url):
            stats["errors"] += 1
            return stats
        try:
            docs: list[dict[str, Any]] = []
            if not seed_only:
                for url, st in (
                    (SEC_LITIGATION_RSS, "sec_litigation_rss"),
                    (SEC_PRESS_RSS, "sec_press_rss"),
                ):
                    try:
                        items = await asyncio.to_thread(_lenient_rss_fetch, client, url)
                        docs.extend(_rss_docs(items, st, days))
                    except Exception as e:
                        logger.warning("rss_fetch_failed url=%s err=%s", url, str(e)[:150])
                stats["feed_items_crypto"] = len(docs)
            docs.extend(_seed_docs())
            stats["seed_docs"] = len(_SEED_CASES)
        except Exception as e:
            logger.warning("regulation_fetch_error: %s", str(e)[:200])
            stats["errors"] += 1
            return stats

        if not docs:
            logger.info("regulation_no_docs_to_ingest")
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
            logger.info("dry_run would_upsert=%d into=%s", stats["embedded"], COLLECTION)
            return stats

        store = QdrantVectorStore(qdrant_url)
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
                source=doc["metadata"].get("source_type") or "regulation",
                severity="high",
                chain="",
            )
            if ok:
                stats["upserted"] += 1
            else:
                stats["errors"] += 1

    logger.info(
        "regulation_done feed=%d seed=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["feed_items_crypto"],
        stats["seed_docs"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Regulatory enforcement → Qdrant")
    parser.add_argument("--dry-run", action="store_true", help="fetch + embed but do not upsert")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only RSS items within the last N days (cron incremental mode)",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="ingest only the curated landmark seed (no RSS fetch)",
    )
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.days, args.seed_only))
    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

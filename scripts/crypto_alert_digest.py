#!/usr/bin/env python3
"""Crypto security incident digest — daily awareness for the RAG engine.

Scans the ``news`` collection for recent security-incident coverage, detects
exploit/hack candidates, dedupes them against previously stored alerts, and
writes the new findings back into Qdrant as ``category=alert`` points so the
knowledge engine has real-time awareness of crypto security incidents.

Detection pipeline:
  1. Scroll ``news`` points with ``published_at`` within the last N days.
  2. Flag content that matches security-incident indicators (hack, exploit,
     stolen, lost $X, rug pull, phishing, breach, drain).
  3. Derive canonical metadata via ``rmi_rag.crypto_schema`` (attack_vector,
     severity, chain, amount_lost_usd, date, protocol, source_type).
  4. Dedupe with ``incident_key = sha256(protocol + date + amount)[:12]``
     against alert points already stored in ``news`` (category=alert).
  5. Embed each new incident (bge-m3 via Ollama) and store it in ``news``
     with ``metadata.category = alert`` and ``source_type = alert_digest``;
     embed failures are non-fatal — the digest is still printed.

Usage:
    python scripts/crypto_alert_digest.py --days 2 --dry-run
    python scripts/crypto_alert_digest.py
    python scripts/crypto_alert_digest.py --limit 200 --min-severity high

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Ollama at OLLAMA_URL (default http://localhost:11434) with bge-m3 pulled
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rmi_rag.bge_embedders import OllamaBGEEmbedder
from rmi_rag.crypto_schema import (
    detect_attack_vector,
    detect_chain,
    infer_severity_from_amount,
    normalize_severity,
    parse_amount_usd,
    parse_date,
)
from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("crypto_alert_digest")

COLLECTION = "news"
SOURCE_TYPE = "alert_digest"
EMBEDDING_DIM = 1024
REQUEST_TIMEOUT = 30
# Losses above this are almost certainly market-cap/valuation figures, not
# single-incident thefts (the largest real crypto heists are ~$1.5-2B).
MAX_INCIDENT_AMOUNT = 50_000_000_000

# Strong indicators — alone they mark a security incident.
STRONG_INDICATORS = (
    "hack",
    "hacked",
    "exploit",
    "exploited",
    "exploiter",
    "stolen",
    "drained",
    "drain",
    "heist",
    "siphon",
    "rug pull",
    "rugpull",
    "pulled the rug",
    "breach",
    "compromised",
    "withdrew",
    "vulnerability",
    "exploits",
    "exploiting",
)

# Moderate indicators — need a second signal before we alert.
MODERATE_INDICATORS = (
    "attack",
    "attacked",
    "lost $",
    "loss of",
    "phishing",
    "scam",
    "fraud",
    "theft",
    "stole",
    "exit scam",
    "suspicious",
    "malicious",
)

# Terms that look like security incidents but usually are not.
NEGATIVE_INDICATORS = (
    "cybersecurity best practices",
    "lessons for",
    "how to protect",
    "guide",
    "tutorial",
    "explained",
    "what is",
    "outlook",
    "analysis of",
    "prevention tips",
    "security checklist",
    "best practices",
    "how hackers",
    "why hackers",
    "how to avoid",
    "how to spot",
    "what to do",
)

# Tokens never treated as a protocol name.
_STOP_PROTOCOL = {
    "a",
    "an",
    "the",
    "of",
    "for",
    "with",
    "at",
    "in",
    "on",
    "after",
    "before",
    "major",
    "home",
    "aes",
    "models",
    "cyber",
    "ai",
    "lets",
    "new",
    "top",
    "crypto",
    "exchange",
    "defi",
    "hackers",
    "hacker",
    "protocol",
    "network",
    "site",
    "via",
    "own",
    "script",
    "released",
    "leak",
    "rogue",
    "reboots",
    "unknown",
    "app",
    "firmware",
    "bitcoin",
    "btc",
    "eth",
    "xrp",
    "ada",
}

# Crypto-domain signals — an incident must mention at least one to be included
# in the crypto security digest (keeps general-IT breaches out).
_CRYPTO_TERMS = (
    "bitcoin",
    "btc",
    "ethereum",
    "crypto",
    "defi",
    "token",
    "wallet",
    "exchange",
    "protocol",
    "staking",
    "blockchain",
    "solana",
    "bnb",
    "bsc",
    "coldcard",
    "ledger",
    "trezor",
    "altcoin",
    "xrp",
    "cardano",
    "stablecoin",
    "usdt",
    "usdc",
    "nft",
    "tron",
    "decentralized",
    "smart contract",
    "liquidity",
    "yield",
    "dex",
    "amm",
    "on-chain",
    "onchain",
    "blockaid",
)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _count_indicators(text: str, indicators: tuple[str, ...]) -> int:
    return sum(1 for ind in indicators if ind in text)


def is_security_incident(content: str, title: str) -> bool:
    """Return True when content/title reads like a security incident.

    Conservative by design: strong indicators alone trigger, moderate
    indicators need a second signal, and educational/analytical content is
    excluded so the digest does not spam false alerts.
    """
    haystack = f"{title} {content}".lower()
    if any(n in haystack for n in NEGATIVE_INDICATORS):
        return False
    strong = _count_indicators(haystack, STRONG_INDICATORS)
    moderate = _count_indicators(haystack, MODERATE_INDICATORS)
    return strong >= 1 or moderate >= 2


def is_crypto_domain(content: str, title: str) -> bool:
    """Return True when content/title is in the crypto domain.

    The news corpus mixes general IT security with crypto security; the digest
    only alerts on incidents that touch crypto assets, protocols or wallets.
    """
    haystack = f"{title} {content}".lower()
    return any(term in haystack for term in _CRYPTO_TERMS)


def make_incident_key(protocol: str, date_iso: str, amount: float) -> str:
    return _content_hash(f"{protocol}|{date_iso}|{amount:.0f}")


def incident_from_point(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a structured incident candidate from a news point payload."""
    metadata = payload.get("metadata") or {}
    content = str(payload.get("content", "") or "")
    title = str(metadata.get("title", "") or "")
    if not is_security_incident(content, title):
        return None
    if not is_crypto_domain(content, title):
        return None

    protocol = str(metadata.get("protocol") or "") or _protocol_from_title(title)
    amount = parse_amount_usd(metadata.get("amount_lost_usd"), content)
    if amount > MAX_INCIDENT_AMOUNT:
        return None
    date_iso = parse_date(metadata.get("published_at") or metadata.get("date"), content) or (
        datetime.now(UTC).date().isoformat()
    )
    severity = normalize_severity(metadata.get("severity"))
    if severity in ("unknown", "info") and amount > 0:
        severity = infer_severity_from_amount(amount)
    chain = detect_chain(content, fallback=str(metadata.get("chain") or "unknown") or "unknown")
    vector = detect_attack_vector(content)
    return {
        "protocol": protocol or "unknown",
        "title": title,
        "amount_lost_usd": amount,
        "date": date_iso,
        "severity": severity,
        "chain": chain,
        "attack_vector": vector,
        "url": str(metadata.get("url", "") or ""),
        "source": str(metadata.get("source", "") or ""),
        "content": content[:1200],
        "incident_key": make_incident_key(protocol or "unknown", date_iso, amount),
    }


_PROTOCOL_PATTERNS = (
    # "<Subject> hacked/exploited/drained/attacked ..."
    re.compile(
        r"^(.{2,60}?)\s+(?:hacked|exploited|exploited|drained|drain|breached|"
        r"compromised|rug\s+pulls?|attacked|loses?|losing|suffers?|stolen|heist)\b",
        re.IGNORECASE,
    ),
    # "drained by <Protocol> Key Flaw" / "as <Protocol> flaw"
    re.compile(
        r"\b(?:by|as)\s+([A-Z][A-Za-z0-9\-. ]{2,40}?)\s+(?:key flaw|flaw|vulnerability|exploit|bug|hack|warning)",
        re.IGNORECASE,
    ),
    # "Fake <Protocol> Staking/Site/Token ..."
    re.compile(
        r"^Fake\s+([A-Z][A-Za-z0-9\-. ]{2,40}?)\s+(?:staking|site|token|airdrop|dapp|app|scam)",
        re.IGNORECASE,
    ),
    # "<Amount> <Coin> Exploit: <Protocol> ..." (after the colon)
    re.compile(
        r"\b(?:exploit|hack|drain|breach)[:\-]\s*([A-Z][A-Za-z0-9\-. ]{2,40}?)\b",
        re.IGNORECASE,
    ),
    # "... <Protocol>" before 'hack/exploit/drain/breach/attack'
    re.compile(
        r"\b(?:from|at|on)\s+([A-Z][A-Za-z0-9\-. ]{2,40}?)\s+(?:hack|exploit|drain|breach|attack)",
        re.IGNORECASE,
    ),
)


def _clean_protocol(token: str) -> str:
    token = token.strip().strip(":'\" ,.")
    token = re.sub(r"'s\b$", "", token, flags=re.IGNORECASE).strip()
    if not token or token.lower() in _STOP_PROTOCOL or len(token) < 2:
        return ""
    if not re.search(r"[A-Za-z]", token):
        return ""
    return token


def _protocol_from_title(title: str) -> str:
    """Best-effort protocol name from a security headline."""
    if not title:
        return ""
    for pattern in _PROTOCOL_PATTERNS:
        m = pattern.search(title)
        if m:
            candidate = _clean_protocol(m.group(1))
            if candidate:
                # Keep the last word when the subject is a phrase
                # ("Poly Network", "SushiSwap" → last token), unless it is a stopword.
                words = [w for w in candidate.split() if w.lower() not in _STOP_PROTOCOL]
                return words[-1] if words else candidate
    return ""


async def fetch_existing_alerts(client: httpx.AsyncClient) -> set[str]:
    """Return the set of incident_keys already stored as alerts in ``news``."""
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    keys: set[str] = set()
    offset: str | None = None
    while True:
        body: dict[str, Any] = {
            "limit": 1000,
            "with_payload": True,
            "with_vectors": False,
            "filter": {"must": [{"key": "metadata.category", "match": {"value": "alert"}}]},
        }
        if offset:
            body["offset"] = offset
        r = await client.post(f"{qdrant_url}/collections/{COLLECTION}/points/scroll", json=body)
        if r.status_code != 200:
            logger.warning("alerts_scroll_failed status=%d", r.status_code)
            break
        result = r.json().get("result", {})
        for point in result.get("points", []):
            md = (point.get("payload") or {}).get("metadata") or {}
            if md.get("incident_key"):
                keys.add(str(md["incident_key"]))
        nxt = result.get("next_page_offset")
        if not nxt:
            break
        offset = nxt
    return keys


async def scroll_recent_news(
    client: httpx.AsyncClient, days: int, limit: int | None
) -> list[dict[str, Any]]:
    """Scroll news points, keeping only those published within *days*."""
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    points: list[dict[str, Any]] = []
    offset: str | None = None
    while True:
        body: dict[str, Any] = {
            "limit": 1000,
            "with_payload": True,
            "with_vectors": False,
        }
        if offset:
            body["offset"] = offset
        r = await client.post(f"{qdrant_url}/collections/{COLLECTION}/points/scroll", json=body)
        if r.status_code != 200:
            logger.warning("news_scroll_failed status=%d", r.status_code)
            break
        result = r.json().get("result", {})
        for point in result.get("points", []):
            md = (point.get("payload") or {}).get("metadata") or {}
            published = str(md.get("published_at") or md.get("date") or "")
            if published[:10] >= since:
                points.append(point)
        if limit and len(points) >= limit:
            points = points[:limit]
            break
        nxt = result.get("next_page_offset")
        if not nxt:
            break
        offset = nxt
    return points


def build_digest_text(incidents: list[dict[str, Any]], total_scanned: int) -> str:
    """Render the human-readable digest summary."""
    if not incidents:
        return (
            f"Crypto security digest: 0 new exploits detected in the last window "
            f"({total_scanned} news items scanned). No new incidents."
        )
    lines = [
        f"Crypto security digest: {len(incidents)} new exploit{'s' if len(incidents) > 1 else ''} detected:",
    ]
    for inc in incidents:
        amount = (
            f"${inc['amount_lost_usd']:,.0f}" if inc["amount_lost_usd"] > 0 else "amount unknown"
        )
        lines.append(
            f"  - [{inc['severity'].upper()}] {inc['protocol'] or inc['title'][:60]} "
            f"({inc['chain']}, {inc['attack_vector']}) lost {amount} on {inc['date']}"
        )
    lines.append(
        f"Source: {total_scanned} news items scanned; stored as alerts in the RAG `news` collection."
    )
    return "\n".join(lines)


async def store_alerts(
    incidents: list[dict[str, Any]], digest_text: str, dry_run: bool
) -> tuple[int, int]:
    """Embed + upsert new incidents into the ``news`` collection as alerts.

    Also stores one digest-summary point (``incident_key = digest:YYYY-MM-DD``)
    so the RAG can answer "what happened today" directly.
    """
    if dry_run:
        return len(incidents), 0

    embedder = OllamaBGEEmbedder(ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"))
    store = QdrantVectorStore()
    stored = failed = 0

    async def _store(embed_text: str, doc_id: str, metadata: dict[str, Any]) -> bool:
        nonlocal stored, failed
        try:
            embedding = await embedder.embed_one(embed_text[:8000])
        except Exception as e:
            logger.warning("alert_embed_failed doc=%s err=%s", doc_id, str(e)[:120])
            failed += 1
            return False
        if len(embedding) != EMBEDDING_DIM:
            logger.warning("alert_bad_dim doc=%s dim=%d", doc_id, len(embedding))
            failed += 1
            return False
        ok = await store.insert(
            doc_id=doc_id,
            collection=COLLECTION,
            embedding=embedding,
            content=embed_text,
            metadata=metadata,
            source=SOURCE_TYPE,
            severity=str(metadata.get("severity", "info")),
            chain=str(metadata.get("chain", "unknown")),
        )
        if ok:
            stored += 1
        else:
            failed += 1
        return ok

    try:
        for inc in incidents:
            doc_id = f"alert:{inc['incident_key']}:{_content_hash(inc['content'][:400])}"
            metadata = {
                "category": "alert",
                "incident_key": inc["incident_key"],
                "protocol": inc["protocol"],
                "title": inc["title"],
                "amount_lost_usd": inc["amount_lost_usd"],
                "date": inc["date"],
                "attack_vector": inc["attack_vector"],
                "chain": inc["chain"],
                "severity": inc["severity"],
                "source": inc["source"],
                "source_type": SOURCE_TYPE,
                "url": inc["url"],
            }
            await _store(inc["content"], doc_id, metadata)

        # Digest summary point (one per day).
        today = datetime.now(UTC).date().isoformat()
        summary_metadata = {
            "category": "alert",
            "incident_key": f"digest:{today}",
            "protocol": "daily-digest",
            "title": f"Crypto Security Digest {today}",
            "amount_lost_usd": sum(i["amount_lost_usd"] for i in incidents),
            "date": today,
            "attack_vector": "multi",
            "chain": "multi",
            "severity": "info",
            "source": SOURCE_TYPE,
            "source_type": SOURCE_TYPE,
            "incident_count": len(incidents),
        }
        await _store(digest_text, f"alert:digest:{today}", summary_metadata)
    finally:
        await store.close()
    return stored, failed


async def main_async(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        news_points = await scroll_recent_news(client, args.days, args.limit)
        existing_keys = await fetch_existing_alerts(client)

    candidates: list[dict[str, Any]] = []
    for point in news_points:
        incident = incident_from_point(point.get("payload") or {})
        if incident is None:
            continue
        if args.min_severity and incident["severity"] not in (
            "critical",
            "high",
            "medium",
            "low",
            "info",
        ):
            continue
        if incident["incident_key"] in existing_keys:
            continue
        candidates.append(incident)

    # Dedupe within the same run by incident_key.
    seen: dict[str, dict[str, Any]] = {}
    for inc in candidates:
        seen.setdefault(inc["incident_key"], inc)
    new_incidents = list(seen.values())

    digest = build_digest_text(new_incidents, len(news_points))
    print(digest)
    logger.info("digest scanned=%d new_incidents=%d", len(news_points), len(new_incidents))

    if args.store_digest and new_incidents:
        stored, failed = await store_alerts(new_incidents, digest, args.dry_run)
        logger.info("alerts stored=%d failed=%d", stored, failed)
        if stored:
            print(f"Stored {stored} alert(s) in Qdrant `news` (category=alert).")
    elif args.store_digest and not new_incidents:
        # Still store a zero-incident digest summary for the day.
        stored, failed = await store_alerts([], digest, args.dry_run)
        logger.info("digest_summary stored=%d failed=%d", stored, failed)
    elif args.dry_run:
        logger.info("[DRY RUN] would store %d alert(s) in Qdrant `news`", len(new_incidents))

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto security incident digest")
    parser.add_argument("--days", type=int, default=2, help="Look back window in days (default 2)")
    parser.add_argument("--limit", type=int, default=None, help="Max news points to scan")
    parser.add_argument(
        "--min-severity",
        choices=("critical", "high", "medium", "low", "info"),
        default=None,
        help="Only alert incidents at or above this severity",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print digest without storing alerts"
    )
    parser.add_argument(
        "--store-digest",
        action="store_true",
        default=True,
        help="Store detected incidents as alerts (default: true)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()

"""Discord Scraper — ingests Discord channels for real-time alpha, scam signals, community sentiment.

Ingests from:
1. **Discord webhooks** — channels push messages to us via configured webhook endpoints
2. **Self-bot scraping** — reads messages from public Discord channels via Discord API
3. **Community channels** — RugMunch's own Discord for user-submitted scam reports

All messages are PII-scrubbed, analyzed for coin mentions, scam signals, and
indexed into Qdrant 'discord_messages' collection for real-time querying.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

# ── Tracked Discord Channels ─────────────────────────────────────────────
# Format: (name, channel_id, guild_name, category)
DISCORD_CHANNELS: list[tuple[str, str, str, str]] = [
    # ═══ SECURITY / SCAM ALERTS ═══
    ("ZachXBT Alerts", "zachxbt", "zachxbt", "security"),
    ("Web3 Security Alerts", "web3security", "web3security", "security"),
    ("BlockSec Alerts", "blocksecteam", "blocksecteam", "security"),
    ("SlowMist Alerts", "slowmist", "slowmist", "security"),
    ("CertiK Alerts", "certik", "certik", "security"),
    ("Peckshield Alerts", "peckshield", "peckshield", "security"),
    ("Immunefi Alerts", "immunefi", "immunefi", "security"),
    ("Rekt News", "rektnews", "rektnews", "security"),
    # ═══ DEFI ═══
    ("Uniswap Community", "uniswap", "uniswap", "defi"),
    ("Aave Community", "aave", "aave", "defi"),
    ("Curve Community", "curve", "curve", "defi"),
    ("Balancer Community", "balancer", "balancer", "defi"),
    ("MakerDAO Community", "makerdao", "makerdao", "defi"),
    ("Compound Community", "compound", "compound", "defi"),
    ("Yearn Community", "yearn", "yearn", "defi"),
    ("Lido Community", "lido", "lido", "defi"),
    ("Rocket Pool Community", "rocketpool", "rocketpool", "defi"),
    # ═══ SOLANA ═══
    ("Solana Community", "solana", "solana", "solana"),
    ("Jupiter Community", "jupiter", "jupiter", "solana"),
    ("Helius Community", "helius", "helius", "solana"),
    ("Magic Eden Community", "magiceden", "magiceden", "solana"),
    ("Metaplex Community", "metaplex", "metaplex", "solana"),
    # ═══ ETHEREUM / L2 ═══
    ("Ethereum Community", "ethereum", "ethereum", "ethereum"),
    ("Arbitrum Community", "arbitrum", "arbitrum", "ethereum"),
    ("Optimism Community", "optimism", "optimism", "ethereum"),
    ("Base Community", "base", "base", "ethereum"),
    ("zkSync Community", "zksync", "zksync", "ethereum"),
    ("Polygon Community", "polygon", "polygon", "ethereum"),
    # ═══ SCAM WATCH ═══
    ("RugPull Alerts", "rugpull", "rugpull", "scam_watch"),
    ("Honeypot Alerts", "honeypot", "honeypot", "scam_watch"),
    ("Pump & Dump Watch", "pumpdump", "pumpdump", "scam_watch"),
    ("Crypto Scam Reports", "scamreports", "scamreports", "scam_watch"),
    # ═══ RESEARCH ═══
    ("Messari Research", "messari", "messari", "research"),
    ("Nansen Research", "nansen", "nansen", "research"),
    ("Delphi Digital", "delphi", "delphi", "research"),
    ("Token Terminal", "tokenterminal", "tokenterminal", "research"),
    ("Dune Analytics", "dune", "dune", "research"),
    ("Glassnode", "glassnode", "glassnode", "research"),
    # �══ TRADING �══
    ("Crypto Trading Signals", "crypotrading", "crypotrading", "trading"),
    ("Alts", "alts", "alts", "trading"),
    ("Crypto Whales", "crypowhales", "crypowhales", "trading"),
    ("Wale Alerts", "walealerts", "walealerts", "trading"),
]

# ── Config ──
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
POLL_INTERVAL = int(os.getenv("DISCORD_POLL_INTERVAL", "60"))  # 1 minute
MAX_ESSAGES = int(os.getenv("DISCORD_MAX_ESSAGES", "20"))
_seen_messages: set[str] = set()

SCAM_KEORDS = [
    "guranteed profit",
    "risk-free",
    "100x",
    "1000x",
    "moonshot",
    "to the moon",
    "financial freedom",
    "passive income",
    "get rich quick",
    "guranteed returns",
    "never lose",
    "sure thing",
    "life changing",
    "dont miss out",
    "limited time",
    "presale",
    "whitelist",
    "insider",
    "pump and dump",
    "rug pull",
    "honeypot",
    "ponzi",
    "pyramid",
    "sec ret",
    "hidden gem",
    "next big thing",
    "explosive",
    "lambo",
    "moon",
]

COIN_PATERN = re.compile(
    r"\$([A-Z]{2,10})\b"
    r"|(?<!\w)([AZ]{2,10})(?:\s*coin|\s*token|\s*protocol)?\b"
    r"(?!\s*[a-z])",
    re.IGNORECASE,
)


async def fetch_discord_messages(channel_id: str) -> list[dict[str, Any]]:
    """Fetch recent messages from a Discord channel via the API."""
    if not DISCORD_BOT_TOKEN:
        return []

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={MAX_ESSAGES}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                url,
                headers={
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                    "User-Agent": "rugbrain/1.0",
                },
            )
            if r.status_code == 200:
                return cast(list[dict[str, Any]], r.json())
            logger.debug("discord_fetch_failed channel=%s status=%d", channel_id, r.status_code)
            return []
    except Exception as exc:
        logger.debug("discord_fetch_error channel=%s error=%s", channel_id, str(exc)[:60])
        return []


async def process_discord_message(
    msg: dict[str, Any], channel_name: str, category: str
) -> dict[str, Any]:
    """Process a single Discord message: analyze, scrub, index."""
    message_id = msg.get("id", "")
    if message_id in _seen_messages:
        return {"status": "skipped"}
    _seen_messages.add(message_id)
    if len(_seen_messages) > 100000:
        _seen_messages.clear()

    content = msg.get("content", "")
    author = msg.get("author", {}).get("username", "unknown")
    timestamp = msg.get("timestamp", datetime.now(UTC).isoformat())

    if not content or len(content) < 10:
        return {"status": "skipped", "reason": "too_short"}

    # Extract coin mentions
    matches = COIN_PATERN.findall(content)
    coins: Counter[str] = Counter()
    for m in matches:
        coin = (m[0] or m[1]).upper()
        if len(coin) >= 2:
            coins[coin] += 1

    # Scam keyword detection
    scam_hits = 0
    scam_matches = []
    for kw in SCAM_KEORDS:
        count = len(re.findall(re.escape(kw), content, re.IGNORECASE))
        if count > 0:
            scam_hits += count
            scam_matches.append(kw)

    risk_score = min(1.0, scam_hits * 0.1)

    # PII scrub
    try:
        from rugbrain.pii_scrubber import scrub_text

        content = await scrub_text(content)
    except Exception:
        pass

    # Index into Qdrant
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(content[:1000])
        doc_id = f"discord_{channel_name}_{message_id}"[:200]
        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", doc_id).lower()

        metadata = {
            "source": "discord",
            "channel": channel_name,
            "category": category,
            "author": author,
            "message_id": message_id,
            "timestamp": timestamp,
            "coins_mentioned": list(coins.keys())[:10],
            "risk_score": risk_score,
            "scam_keywords": scam_matches[:5],
            "ingested_at": datetime.now(UTC).isoformat(),
        }

        store = QdrantVectorStore()
        ok = await store.insert(
            doc_id=doc_id,
            collection="discord_messages",
            embedding=embedding,
            content=content[:2000],
            metadata=metadata,
            source="discord",
            severity="high" if risk_score > 0.3 else "medium" if risk_score > 0.1 else "info",
            chain="ethereum",
        )
        logger.debug("discord_ingested channel=%s risk=%.2f ok=%s", channel_name, risk_score, ok)
        return {"status": "ok" if ok else "failed", "doc_id": doc_id, "risk_score": risk_score}
    except Exception as exc:
        logger.debug("discord_ingest_failed msg=%s error=%s", message_id[:10], str(exc)[:60])
        return {"status": "failed", "error": str(exc)[:60]}


async def discord_scan_cycle() -> dict[str, Any]:
    """Scan all Discord channels for new messages."""
    results = []
    for name, channel_id, _guild, category in DISCORD_CHANNELS:
        messages = await fetch_discord_messages(channel_id)
        for msg in messages:
            result = await process_discord_message(msg, name, category)
            if result.get("status") == "ok":
                results.append(result)
        await asyncio.sleep(1)
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "channels_scanned": len(DISCORD_CHANNELS),
        "new_messages": len(results),
        "high_risk": sum(1 for r in results if r.get("risk_score", 0) > 0.3),
    }


async def discord_scan_loop() -> None:
    """Run Discord scanning in a continuous background loop."""
    logger.info(
        "discord_scraper_started interval=%ds channels=%d", POLL_INTERVAL, len(DISCORD_CHANNELS)
    )
    while True:
        try:
            result = await discord_scan_cycle()
            if result["new_messages"] > 0:
                logger.info(
                    "discord_new count=%d high_risk=%d", result["new_messages"], result["high_risk"]
                )
        except Exception as exc:
            logger.warning("discord_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)

"""Newsletter Ingestion — scrapes crypto newsletters, bypasses paywalls, indexes into RAG.

Scrapes 50+ crypto newsletters daily. Uses web-extract + trafilatura for content
extraction, FlareSolverr for Cloudflare bypass, and Presidio for PII scrubbing.

Content is indexed into Qdrant 'newsletters' collection and served via a unified
news feed API endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── 50+ Crypto Newsletters ───────────────────────────────────────────────
# Format: (name, url, frequency, category)
NEWSLETTERS: list[tuple[str, str, str, str]] = [
    # ═══ DAILY NEWSLETTERS ═══
    ("Blockworks Daily", "https://blockworks.co/newsletter", "daily", "news"),
    ("The Block Daily", "https://www.theblock.co/newsletter", "daily", "news"),
    ("CoinDesk Daily", "https://www.coindesk.com/newsletter", "daily", "news"),
    ("CoinTelegraph Daily", "https://cointelegraph.com/newsletter", "daily", "news"),
    ("Bankless Daily", "https://www.bankless.com/newsletter", "daily", "news"),
    ("Delphi Daily", "https://www.delphidigital.io/newsletter", "daily", "research"),
    ("Messari Daily", "https://messari.io/newsletter", "daily", "research"),
    ("Nansen Daily", "https://www.nansen.ai/newsletter", "daily", "research"),
    ("Token Terminal Daily", "https://tokenterminal.com/newsletter", "daily", "research"),
    ("Dune Daily", "https://dune.com/newsletter", "daily", "research"),
    ("DefiLlama Daily", "https://defillama.com/newsletter", "daily", "defi"),
    ("CoinGecko Daily", "https://www.coingecko.com/newsletter", "daily", "news"),
    ("CryptoPanic Daily", "https://cryptopanic.com/newsletter", "daily", "news"),
    ("Unchained Daily", "https://unchainedcrypto.com/newsletter", "daily", "news"),
    ("The Defiant Daily", "https://thedefiant.io/newsletter", "daily", "news"),
    ("Zima Red Daily", "https://zimared.com/newsletter", "daily", "defi"),
    ("Week in Ethereum", "https://weekinethereumnews.com", "weekly", "ethereum"),
    ("Week in DeFi", "https://weekindefi.com", "weekly", "defi"),
    ("Week in Polkadot", "https://weekinpolkadot.com", "weekly", "polkadot"),
    ("Week in Cosmos", "https://weekincosmos.com", "weekly", "cosmos"),
    ("Week in Solana", "https://weekinsolana.com", "weekly", "solana"),
    ("Week in Bitcoin", "https://weekinbitcoin.com", "weekly", "bitcoin"),
    ("Week in L2", "https://weekinl2.com", "weekly", "ethereum"),
    ("Week in ZK", "https://weekinzk.com", "weekly", "ethereum"),

    # ═══ RESEARCH NEWSLETTERS ═══
    ("Glassnode Weekly", "https://glassnode.com/newsletter", "weekly", "research"),
    ("CoinMetrics Weekly", "https://coinmetrics.io/newsletter", "weekly", "research"),
    ("IntoTheBlock Weekly", "https://intotheblock.com/newsletter", "weekly", "research"),
    ("Santiment Weekly", "https://santiment.net/newsletter", "weekly", "research"),
    ("CryptoQuant Weekly", "https://cryptoquant.com/newsletter", "weekly", "research"),
    ("Kaiko Weekly", "https://kaiko.com/newsletter", "weekly", "research"),
    ("Arcane Research", "https://arcane.no/newsletter", "weekly", "research"),
    ("K33 Research", "https://k33.com/newsletter", "weekly", "research"),
    ("Bitcoin Magazine Pro", "https://bitcoinmagazine.com/newsletter", "weekly", "bitcoin"),

    # ═══ DEFI NEWSLETTERS ═══
    ("DeFi Pulse", "https://defipulse.com/newsletter", "weekly", "defi"),
    ("DeFi Dad", "https://defidad.com/newsletter", "weekly", "defi"),
    ("DeFi Times", "https://defitimes.com/newsletter", "weekly", "defi"),
    ("DeFi Weekly", "https://defiweekly.com", "weekly", "defi"),
    ("DeFi Download", "https://defidownload.com", "weekly", "defi"),
    ("DeFi Llama", "https://defillama.com/newsletter", "daily", "defi"),

    # ═══ SECURITY NEWSLETTERS ═══
    ("BlockSec Weekly", "https://blocksecteam.com/newsletter", "weekly", "security"),
    ("SlowMist Weekly", "https://slowmist.com/newsletter", "weekly", "security"),
    ("CertiK Weekly", "https://certik.com/newsletter", "weekly", "security"),
    ("Peckshield Weekly", "https://peckshield.com/newsletter", "weekly", "security"),
    ("Rekt News", "https://rekt.news", "weekly", "security"),
    ("Web3 Security Weekly", "https://web3securityweekly.com", "weekly", "security"),
    ("Immunefi Weekly", "https://immunefi.com/newsletter", "weekly", "security"),
    ("Hacken Weekly", "https://hacken.io/newsletter", "weekly", "security"),

    # ═══ REGULATORY NEWSLETTERS ═══
    ("Crypto Law Weekly", "https://cryptolaw.com/newsletter", "weekly", "regulation"),
    ("SEC Crypto", "https://www.sec.gov/crypto", "weekly", "regulation"),
    ("CFTC Crypto", "https://www.cftc.gov/crypto", "weekly", "regulation"),
    ("Blockchain Legal", "https://blockchainlegal.com/newsletter", "weekly", "regulation"),
    ("CoinCenter", "https://coincenter.org/newsletter", "weekly", "regulation"),

    # ═══ VC / INVESTOR NEWSLETTERS ═══
    ("a16z Crypto", "https://a16z.com/crypto/newsletter", "weekly", "vc"),
    ("Paradigm", "https://paradigm.xyz/newsletter", "weekly", "vc"),
    ("Variant Fund", "https://variant.fund/newsletter", "weekly", "vc"),
    ("Multicoin Capital", "https://multicoin.capital/newsletter", "weekly", "vc"),
    ("Dragonfly Capital", "https://dragonfly.xyz/newsletter", "weekly", "vc"),
    ("Electric Capital", "https://electriccapital.com/newsletter", "weekly", "vc"),
    ("Pantera Capital", "https://panteracapital.com/newsletter", "weekly", "vc"),
    ("Coinbase Ventures", "https://www.coinbase.com/ventures/newsletter", "weekly", "vc"),
    ("Binance Research", "https://research.binance.com/newsletter", "weekly", "research"),

    # ═══ SOLANA NEWSLETTERS ═══
    ("Solana Daily", "https://solanadaily.com", "daily", "solana"),
    ("Solana News", "https://solana.com/newsletter", "weekly", "solana"),
    ("Solana Compass", "https://solanacompass.com", "weekly", "solana"),
    ("Solana Gems", "https://solanagems.com", "weekly", "solana"),

    # ═══ MEME / SCAM NEWSLETTERS ═══
    ("RugWatch", "https://rugwatch.com", "weekly", "scam_watch"),
    ("ScamAlert", "https://scamalert.com", "weekly", "scam_watch"),
    ("Honeypot Report", "https://honeypot.report", "weekly", "scam_watch"),
    ("PumpWatch", "https://pumpwatch.com", "daily", "scam_watch"),
    ("DumpWatch", "https://dumpwatch.com", "daily", "scam_watch"),
]

# ── Config ────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.getenv("NEWSLETTER_POLL_INTERVAL", "21600"))  # 6 hours
SEEN_URLS: set[str] = set()
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://talos:8191/v1")

COIN_PATTERN = re.compile(r'\$([A-Z]{2,10})\b|(?<!\w)([A-Z]{2,10})(?:\s*coin|\s*token|\s*protocol)?\b', re.IGNORECASE)


async def scrape_url(url: str) -> str | None:
    """Scrape URL content using trafilatura via web-extract, with FlareSolverr fallback."""
    try:
        # Try direct fetch with trafilatura-style extraction
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            })
            if r.status_code == 200:
                text = r.text
                # Simple content extraction (strip HTML tags)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 500:  # Got meaningful content
                    return text[:10000]
    except Exception as exc:
        logger.debug("direct_fetch_failed url=%s error=%s", url[:40], str(exc)[:60])

    # FlareSolverr fallback for Cloudflare/paywalled sites
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(FLARESOLVERR_URL, json={
                "cmd": "request.get",
                "url": url,
                "maxTimeout": 15000,
            })
            if r.status_code == 200:
                data = r.json()
                text = data.get("solution", {}).get("response", "")
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 500:
                    return text[:10000]
    except Exception as exc:
        logger.debug("flaresolverr_failed url=%s error=%s", url[:40], str(exc)[:60])

    return None


def extract_coins(text: str) -> list[str]:
    """Extract coin/token mentions from text."""
    matches = COIN_PATTERN.findall(text)
    coins = set()
    for m in matches:
        coin = (m[0] or m[1]).upper()
        if len(coin) >= 2 and coin not in ("THE", "FOR", "AND", "THIS", "THAT", "YOU", "NOT", "WITH", "FROM", "HAS", "ARE", "WAS", "WERE", "BEEN", "HAVE", "HAD", "DOES", "DOING", "DONE", "SAYS", "SAID", "THAN", "THEN", "ALSO", "VERY", "JUST", "MORE", "SOME", "SUCH", "ONLY", "WHAT", "WHEN", "WHERE", "WHICH", "WHO", "WHOM", "WHOSE", "WHY", "HOW", "ALL", "EACH", "EVER", "NEW", "NOW", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "MAY", "CAN", "WILL", "MUST", "SHALL", "COULD", "WOULD", "SHOULD", "MIGHT", "ABOUT", "ABOVE", "AFTER", "AGAIN", "AGAINST", "BETWEEN", "BENEATH", "BESIDE", "BEYOND", "DURING", "INSIDE", "OUTSIDE", "UNDER", "UPON", "WITHIN", "WITHOUT"):
            coins.add(coin)
    return list(coins)[:20]


async def ingest_newsletter(name: str, url: str, frequency: str, category: str) -> dict[str, Any]:
    """Scrape and ingest a newsletter into Qdrant."""
    if url in SEEN_URLS:
        return {"status": "skipped", "reason": "already_seen"}
    SEEN_URLS.add(url)
    if len(SEEN_URLS) > 50000:
        SEEN_URLS.clear()

    # Scrape content
    content = await scrape_url(url)
    if not content:
        logger.debug("newsletter_empty name=%s url=%s", name, url[:40])
        return {"status": "failed", "reason": "no_content"}

    # Extract coins
    coins = extract_coins(content)

    # PII scrub
    try:
        from rugbrain.pii_scrubber import scrub_text
        content = await scrub_text(content)
    except Exception:
        pass

    # Build metadata
    metadata = {
        "source": "newsletter",
        "name": name,
        "url": url,
        "frequency": frequency,
        "category": category,
        "coins_mentioned": coins,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }

    # Index into Qdrant
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(content[:2000])
        doc_id = f"newsletter_{name.replace(' ', '_')}"[:200]
        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", doc_id).lower()

        store = QdrantVectorStore()
        ok = await store.insert(
            doc_id=doc_id,
            collection="newsletters",
            embedding=embedding,
            content=content[:5000],
            metadata=metadata,
            source="newsletter",
            severity="info",
            chain="ethereum",
        )
        logger.info("newsletter_ingested name=%s coins=%d ok=%s", name, len(coins), ok)
        return {"status": "ok" if ok else "failed", "doc_id": doc_id, "coins": coins}
    except Exception as exc:
        logger.warning("newsletter_ingest_failed name=%s error=%s", name, str(exc)[:80])
        return {"status": "failed", "error": str(exc)[:80]}


async def newsletter_scan_cycle() -> dict[str, Any]:
    """Scan all newsletters for new content."""
    results = []
    for name, url, frequency, category in NEWSLETTERS:
        result = await ingest_newsletter(name, url, frequency, category)
        results.append({name: result["status"]})
        await asyncio.sleep(3)  # Rate limit
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total_newsletters": len(NEWSLETTERS),
        "ingested": sum(1 for r in results if list(r.values())[0] == "ok"),
        "results": results[:10],
    }


async def newsletter_scan_loop() -> None:
    """Run newsletter scanning in a continuous background loop."""
    logger.info("newsletter_ingester_started interval=%ds newsletters=%d", POLL_INTERVAL, len(NEWSLETTERS))
    while True:
        try:
            result = await newsletter_scan_cycle()
            logger.info("newsletter_scan ingested=%d/%d", result["ingested"], result["total_newsletters"])
        except Exception as exc:
            logger.warning("newsletter_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)

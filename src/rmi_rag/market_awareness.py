"""RugBrain — market awareness for new token scans.

Tracks the current crypto-security landscape using the free DataBus/MCP-style
APIs (CoinGecko, DeFiLlama, DexScreener) and feeds that awareness into RAG
scoring for new token scans:

  * ``trending_scams``   — newly launched / suspicious tokens (DexScreener
                           token profiles + CoinGecko recently-added) that
                           exhibit rug-pull warning signs (no website, no
                           socials, tiny market cap).
  * ``recent_exploits``  — protocols exploited in the last
                           ``AWARENESS_EXPLOIT_WINDOW_DAYS`` days from the
                           DeFiLlama hacks database.
  * ``market_sentiment`` — ``fearful | neutral | greedy`` from the
                           Alternative.me Fear & Greed index.

``score_awareness(query)`` returns a 0..1 signal used to boost RAG results
for tokens that match a recently exploited protocol or a trending scam —
so the scanner is always aware of live security incidents.

Results are cached in Redis (``rag:awareness:snapshot``, TTL
``AWARENESS_CACHE_TTL``) to stay well inside free-tier rate limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger("rmi_rag.market_awareness")

DEFILLAMA_HACKS_URL = os.getenv("DEFILLAMA_HACKS_URL", "https://api.llama.fi/hacks")
COINGECKO_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=gecko_desc&per_page=25&sparkline=false"
)
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
FNG_URL = os.getenv("FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=1")

EXPLOIT_WINDOW_DAYS = float(os.getenv("AWARENESS_EXPLOIT_WINDOW_DAYS", "7"))
CACHE_TTL = int(os.getenv("AWARENESS_CACHE_TTL", "900"))
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
CACHE_KEY = os.getenv("AWARENESS_CACHE_KEY", "rag:awareness:snapshot")


# ── Helpers ──────────────────────────────────────────────────────────────


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()


def _recent(ts: float, window_days: float) -> bool:
    if ts <= 0:
        return False
    try:
        age = (datetime.now(UTC) - datetime.fromtimestamp(ts, tz=UTC)).days
        return 0 <= age <= window_days
    except (OverflowError, OSError, ValueError):
        return False


def _fng_classification(value: float) -> str:
    if value < 30:
        return "fearful"
    if value > 70:
        return "greedy"
    return "neutral"


def _suspicious_profile(profile: dict[str, Any]) -> bool:
    """Heuristic: a new token profile with no website and no social links.

    Not proof of a rug pull — but the absence of a website/socials on a
    newly promoted token is the classic pump-and-dump / exit-scam signal
    and worth flagging for downstream scrutiny.
    """
    links = profile.get("links") or []
    labels = {str(link.get("label", "")).lower() for link in links if isinstance(link, dict)}
    has_website = any(
        "website" in label or "twitter" in label or "telegram" in label for label in labels
    )
    return not has_website


# ── Source fetchers ──────────────────────────────────────────────────────


async def fetch_recent_exploits(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Protocols exploited within the awareness window (DeFiLlama hacks DB)."""
    exploits: list[dict[str, Any]] = []
    try:
        r = await client.get(DEFILLAMA_HACKS_URL)
        if r.status_code != 200:
            log.warning("awareness_defillama_failed status=%d", r.status_code)
            return exploits
        for hack in r.json() or []:
            if not isinstance(hack, dict):
                continue
            ts = float(hack.get("date") or 0)
            if not _recent(ts, EXPLOIT_WINDOW_DAYS):
                continue
            name = str(hack.get("name", "")).strip() or "unknown"
            amount = float(hack.get("amount") or 0)
            chains = hack.get("chain") or []
            chain = str(chains[0]).lower() if isinstance(chains, list) and chains else "unknown"
            exploits.append(
                {
                    "protocol": name,
                    "amount_lost_usd": amount,
                    "date": _ts_to_iso(ts),
                    "technique": str(hack.get("technique", "")).strip(),
                    "chain": chain,
                    "classification": str(hack.get("classification", "")).strip(),
                }
            )
        exploits.sort(key=lambda x: x["date"], reverse=True)
    except Exception as e:
        log.warning("awareness_defillama_error: %s", e)
    return exploits[:20]


async def fetch_trending_scams(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Suspicious new tokens from DexScreener + CoinGecko recently-added."""
    scams: list[dict[str, Any]] = []
    seen: set[str] = set()

    async def _add(token_id: str, name: str, chain: str, reasons: list[str], url: str) -> None:
        key = token_id or name.lower()
        if key in seen:
            return
        seen.add(key)
        scams.append(
            {
                "token": name,
                "chain": chain,
                "reasons": reasons,
                "url": url,
            }
        )

    try:
        r = await client.get(DEXSCREENER_PROFILES_URL)
        if r.status_code == 200:
            for profile in r.json() or []:
                if not isinstance(profile, dict):
                    continue
                name = str(profile.get("tokenAddress", "")).strip()[:12] or "unknown"
                chain = str(profile.get("chainId", "")).strip()
                reasons = []
                if _suspicious_profile(profile):
                    reasons.append("no_website_or_socials")
                if not reasons:
                    continue
                url = str(profile.get("url", ""))
                await _add(f"ds:{profile.get('tokenAddress', '')}", name, chain, reasons, url)
    except Exception as e:
        log.warning("awareness_dexscreener_error: %s", e)

    try:
        r = await client.get(COINGECKO_MARKETS_URL)
        if r.status_code == 200:
            for coin in r.json() or []:
                if not isinstance(coin, dict):
                    continue
                name = str(coin.get("name", "")).strip()
                market_cap = float(coin.get("market_cap") or 0)
                reasons = []
                if 0 < market_cap < 500_000:
                    reasons.append("tiny_market_cap")
                if not reasons:
                    continue
                await _add(
                    f"cg:{coin.get('id', '')}",
                    name,
                    "unknown",
                    reasons,
                    f"https://www.coingecko.com/en/coins/{coin.get('id', '')}",
                )
    except Exception as e:
        log.warning("awareness_coingecko_error: %s", e)

    return scams[:25]


async def fetch_market_sentiment(client: httpx.AsyncClient) -> str:
    """Fear & Greed classification; neutral on any failure."""
    try:
        r = await client.get(FNG_URL)
        if r.status_code != 200:
            return "neutral"
        data = r.json() or {}
        values = data.get("data") or []
        if not values:
            return "neutral"
        value = float(values[0].get("value") or 50)
        return _fng_classification(value)
    except Exception as e:
        log.warning("awareness_fng_error: %s", e)
        return "neutral"


# ── Snapshot + cache ─────────────────────────────────────────────────────


async def _redis_get() -> str | None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        try:
            value = await r.get(CACHE_KEY)
            return str(value) if value is not None else None
        finally:
            await r.aclose()
    except Exception:
        return None


async def _redis_set(payload: dict[str, Any]) -> None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        try:
            await r.set(CACHE_KEY, json.dumps(payload), ex=CACHE_TTL)
        finally:
            await r.aclose()
    except Exception as e:
        log.debug("awareness_cache_write_failed: %s", e)


async def market_awareness_snapshot(
    client: httpx.AsyncClient | None = None, use_cache: bool = True
) -> dict[str, Any]:
    """Build (or fetch cached) market-awareness snapshot.

    Returns ``{"trending_scams": [...], "recent_exploits": [...],
    "market_sentiment": "fearful|neutral|greedy"}``.
    """
    if use_cache:
        cached = await _redis_get()
        if cached:
            try:
                loaded: dict[str, Any] = json.loads(cached)
                return loaded
            except json.JSONDecodeError:
                pass

    own_client = client is None
    c = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    try:
        recent_exploits, trending_scams, sentiment = await asyncio.gather(
            fetch_recent_exploits(c),
            fetch_trending_scams(c),
            fetch_market_sentiment(c),
        )
    finally:
        if own_client and c is not None:
            await c.aclose()

    snapshot: dict[str, Any] = {
        "trending_scams": trending_scams,
        "recent_exploits": recent_exploits,
        "market_sentiment": sentiment,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    if use_cache:
        await _redis_set(snapshot)
    return snapshot


# ── Scoring feed for new token scans ─────────────────────────────────────


def score_awareness(
    query: str,
    snapshot: dict[str, Any] | None = None,
    default_score: float = 0.5,
) -> float:
    """Return a 0..1 awareness boost for *query*.

    - 1.0  — the query names a protocol from ``recent_exploits`` or a token
             from ``trending_scams`` (live incident awareness).
    - 0.75 — the query contains scam/exploit keywords matching the current
             sentiment (fearful market).
    - ``default_score`` (0.5) — nothing notable.
    """
    if not query:
        return default_score
    lowered = query.lower()
    reasons: list[str] = []
    if snapshot:
        for exploit in snapshot.get("recent_exploits", []) or []:
            protocol = str(exploit.get("protocol", "")).lower()
            if protocol and protocol in lowered:
                reasons.append(f"recent_exploit:{protocol}")
        for scam in snapshot.get("trending_scams", []) or []:
            token = str(scam.get("token", "")).lower()
            if token and token in lowered:
                reasons.append(f"trending_scam:{token}")
    if reasons:
        return 1.0
    if (
        snapshot
        and snapshot.get("market_sentiment") == "fearful"
        and any(k in lowered for k in ("scam", "rug", "hack", "exploit", "phish", "honeypot"))
    ):
        return 0.75
    return default_score


def boosted_risk(query: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """One-call helper: awareness score + matched reasons for a token query."""
    reasons: list[str] = []
    if snapshot:
        for exploit in snapshot.get("recent_exploits", []) or []:
            protocol = str(exploit.get("protocol", "")).lower()
            if protocol and protocol in query.lower():
                reasons.append(f"recent_exploit:{protocol}")
        for scam in snapshot.get("trending_scams", []) or []:
            token = str(scam.get("token", "")).lower()
            if token and token in query.lower():
                reasons.append(f"trending_scam:{token}")
    return {
        "score": score_awareness(query, snapshot),
        "reasons": reasons,
        "market_sentiment": (snapshot or {}).get("market_sentiment", "neutral"),
    }


__all__ = [
    "boosted_risk",
    "fetch_market_sentiment",
    "fetch_recent_exploits",
    "fetch_trending_scams",
    "market_awareness_snapshot",
    "score_awareness",
]

"""RugBrain market data enrichment — CoinPaprika + LunarCrush.

Adds two free-tier market/social data providers on top of the existing
CoinGecko / DeFiLlama / DexScreener awareness sources:

  * **CoinPaprika** — ``https://api.coinpaprika.com`` — free, no auth, no
    meaningful rate limit. Supplies tickers (price / 24h change / volume /
    market cap) and newly-listed coins.
  * **LunarCrush** — ``https://lunarcrush.com/api/v2`` — free tier with an
    API key (``LUNARCRUSH_API_KEY``). Supplies social score + sentiment per
    symbol. Degrades gracefully (empty results) when the key is absent.

Helpers build the pieces used by ``market_snapshot``:

  * ``top_gainers`` / ``top_losers``  — 24h % change extremes
  * ``most_traded``                  — highest 24h USD volume
  * ``new_listings``                 — recently added coins
  * ``market_ticker_snapshot()``     — combined market overview
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

COINPAPRIKA_TICKERS_URL = "https://api.coinpaprika.com/v1/tickers"
COINPAPRIKA_NEW_URL = "https://api.coinpaprika.com/v1/coins/new"
LUNARCRUSH_URL = "https://lunarcrush.com/api/v2"
LUNARCRUSH_KEY = os.getenv("LUNARCRUSH_API_KEY", "").strip()
LUNARCRUSH_ASSETS = os.getenv(
    "LUNARCRUSH_ASSETS", "BTC,ETH,SOL,DOGE,ARB,OP,LINK,AAVE,UNI,SUI"
).split(",")
HTTP_TIMEOUT = int(os.getenv("CRAWLER_HTTP_TIMEOUT", "30"))
USER_AGENT = "RMI-RAG/1.0 (+https://rugmunch.io; market-data)"


async def fetch_coinpaprika_tickers(
    client: httpx.AsyncClient | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Top coins by rank from CoinPaprika (free, no auth)."""
    own = client is None
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        r = await http.get(COINPAPRIKA_TICKERS_URL, params={"limit": limit})
        if r.status_code != 200:
            log.warning("coinpaprika_tickers_status status=%d", r.status_code)
            return []
        tickers: list[dict[str, Any]] = []
        for t in r.json() or []:
            if not isinstance(t, dict):
                continue
            quote = (t.get("quotes") or {}).get("USD") or {}
            tickers.append(
                {
                    "symbol": str(t.get("symbol", "")).upper(),
                    "name": str(t.get("name", "")),
                    "rank": int(t.get("rank") or 0),
                    "price_usd": float(quote.get("price") or 0.0),
                    "volume_24h_usd": float(quote.get("volume_24h") or 0.0),
                    "market_cap_usd": float(quote.get("market_cap") or 0.0),
                    "percent_change_24h": float(quote.get("percent_change_24h") or 0.0),
                }
            )
        return tickers
    except Exception as e:
        log.warning("coinpaprika_tickers_error: %s", str(e)[:120])
        return []
    finally:
        if own:
            await http.aclose()


async def fetch_coinpaprika_new(
    client: httpx.AsyncClient | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    """Recently-added coins on CoinPaprika."""
    own = client is None
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        r = await http.get(COINPAPRIKA_NEW_URL)
        if r.status_code != 200:
            return []
        coins: list[dict[str, Any]] = []
        for c in (r.json() or [])[:limit]:
            if not isinstance(c, dict):
                continue
            coins.append(
                {
                    "id": str(c.get("id", "")),
                    "name": str(c.get("name", "")),
                    "symbol": str(c.get("symbol", "")).upper(),
                    "is_new": bool(c.get("is_new")),
                    "type": str(c.get("type", "")),
                }
            )
        return coins
    except Exception as e:
        log.warning("coinpaprika_new_error: %s", str(e)[:120])
        return []
    finally:
        if own:
            await http.aclose()


async def fetch_lunarcrush_social(
    client: httpx.AsyncClient | None = None, symbols: list[str] | None = None
) -> list[dict[str, Any]]:
    """LunarCrush social metrics (score + sentiment) for tracked symbols.

    Returns [] when LUNARCRUSH_API_KEY is unset or the API is unreachable.
    The key is read at call time so it can be configured per-process.
    """
    key = os.getenv("LUNARCRUSH_API_KEY", "").strip()
    if not key:
        log.debug("lunarcrush_disabled reason=no LUNARCRUSH_API_KEY")
        return []
    own = client is None
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        r = await http.get(
            LUNARCRUSH_URL,
            params={"data": "assets", "symbols": ",".join(symbols or LUNARCRUSH_ASSETS)},
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code != 200:
            log.warning("lunarcrush_status status=%d", r.status_code)
            return []
        rows: list[dict[str, Any]] = []
        for item in (r.json() or {}).get("data") or []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "symbol": str(item.get("symbol", "")).upper(),
                    "social_score": float(item.get("social_score") or 0.0),
                    "sentiment": float(item.get("sentiment") or 0.0),
                    "social_volume_24h": float(item.get("social_volume_24h") or 0.0),
                    "galaxy_score": float(item.get("galaxy_score") or 0.0),
                }
            )
        return rows
    except Exception as e:
        log.warning("lunarcrush_error: %s", str(e)[:120])
        return []
    finally:
        if own:
            await http.aclose()


# ── Derived views ────────────────────────────────────────────────────────


def top_gainers(tickers: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return sorted(tickers, key=lambda t: t["percent_change_24h"], reverse=True)[:limit]


def top_losers(tickers: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return sorted(tickers, key=lambda t: t["percent_change_24h"])[:limit]


def most_traded(tickers: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return sorted(tickers, key=lambda t: t["volume_24h_usd"], reverse=True)[:limit]


def _social_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r["symbol"]).upper(): r for r in rows}


def enrich_with_social(
    tickers: list[dict[str, Any]], social: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge LunarCrush social metrics into ticker rows by symbol."""
    social_by_symbol = _social_map(social)
    merged: list[dict[str, Any]] = []
    for t in tickers:
        row = dict(t)
        s = social_by_symbol.get(row["symbol"])
        if s:
            row["social_score"] = s["social_score"]
            row["social_sentiment"] = s["sentiment"]
        merged.append(row)
    return merged


async def market_ticker_snapshot(
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Combined market overview: gainers, losers, volume, new listings, social."""
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        tickers, new_coins, social = await asyncio.gather(
            fetch_coinpaprika_tickers(http),
            fetch_coinpaprika_new(http),
            fetch_lunarcrush_social(http),
        )
    finally:
        if client is None:
            await http.aclose()
    enriched = enrich_with_social(tickers, social)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "top_gainers_24h": top_gainers(enriched, 10),
        "top_losers_24h": top_losers(enriched, 10),
        "most_traded_24h": most_traded(enriched, 10),
        "newly_listed": new_coins,
        "social_metrics": social,
        "ticker_count": len(tickers),
    }


__all__ = [
    "enrich_with_social",
    "fetch_coinpaprika_new",
    "fetch_coinpaprika_tickers",
    "fetch_lunarcrush_social",
    "market_ticker_snapshot",
    "most_traded",
    "top_gainers",
    "top_losers",
]

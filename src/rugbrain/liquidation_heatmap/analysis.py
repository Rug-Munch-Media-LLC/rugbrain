"""Analysis functions for liquidation data."""

from __future__ import annotations

from typing import Any

from rugbrain.liquidation_heatmap.price_bands import (
    get_hype_liquidations as _get_hype,
)
from rugbrain.liquidation_heatmap.price_bands import (
    get_liquidation_wins as _get_wins,
)

__all__ = [
    "get_hype_liquidations",
    "get_liquidation_wins",
    "get_total_market_liq_24h",
]


async def get_total_market_liq_24h() -> dict[str, Any]:
    """Get total market liquidation volume from Coinglass-style aggregation.

    Estimates from available CEX data + protocol data.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if r.status_code == 200:
                tickers = r.json()
                total_btc_vol = 0.0
                for t in tickers:
                    if t["symbol"] in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
                        total_btc_vol += float(t.get("quoteVolume", "0"))
                return {
                    "binance_24h_vol": round(total_btc_vol, 2),
                    "estimated_total_24h": round(total_btc_vol * 2.5, 2),  # Binance ~40% of market
                    "source": "binance_ticker",
                }
    except Exception:
        pass
    return {"binance_24h_vol": 0, "estimated_total_24h": 0, "source": "unavailable"}


def get_hype_liquidations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify hype liquidations — large positions with social media mentions.

    Flags: >$1M single position, >$100k on meme coins, liquidator profit >$50k.
    """
    return _get_hype(events)


def get_liquidation_wins(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify liquidation wins — profitable liquidator opportunities.

    Flags: positions where liquidator could profit from the spread.
    """
    return _get_wins(events)

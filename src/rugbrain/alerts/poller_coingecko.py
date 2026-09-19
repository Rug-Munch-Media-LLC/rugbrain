"""CoinGecko new-token-launch poller."""

from __future__ import annotations

import logging

import httpx

from . import config
from .models import SecurityAlert

log = logging.getLogger("rugbrain.alerts")


async def poll_coingecko(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """New token launches from CoinGecko (recently-added / gecko_desc)."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(config.COINGECKO_MARKETS_URL)
        if r.status_code != 200:
            log.warning("coingecko_poll_failed status=%d", r.status_code)
            return alerts
        for coin in r.json() or []:
            if not isinstance(coin, dict):
                continue
            name = str(coin.get("name", "")).strip()
            symbol = str(coin.get("symbol", "")).strip().upper()
            if not name:
                continue
            price = coin.get("current_price") or 0
            mc = coin.get("market_cap") or 0
            description = (
                f"Newly listed token {name} ({symbol}) with market cap "
                f"${mc:,.0f} and current price ${price:,.6f}. "
                "New launches are frequently targeted for pump-and-dump and "
                "rug-pull schemes; verify liquidity locks and holder "
                "distribution before trading."
            )
            alerts.append(
                SecurityAlert(
                    source="coingecko",
                    title=f"New token launch: {name} ({symbol})",
                    description=description,
                    url=f"https://www.coingecko.com/en/coins/{coin.get('id', '')}",
                    severity="low",
                    attack_vector="rug-pull",
                    chain="unknown",
                    protocol=name,
                    date="",
                    raw={"coin_id": coin.get("id", ""), "symbol": symbol},
                )
            )
    except Exception as e:
        log.warning("coingecko_poll_error: %s", e)
    return alerts

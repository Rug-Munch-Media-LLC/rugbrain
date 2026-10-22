"""Number-first content units built from Hyperliquid liquidation + whale data.

Every unit names the endpoint field it quotes, so a claim can be re-derived by a
reviewer.  A unit whose numbers are missing is dropped rather than filled with
adjectives — the failure mode this module exists to prevent.
"""

from __future__ import annotations

from typing import Any

EXPLORER = "https://app.hyperliquid.xyz/explorer/address/{wallet}"
TRADE_PAGE = "https://app.hyperliquid.xyz/trade/{coin}"

UNITS: dict[str, dict[str, Any]] = {
    "liq_cascade": {
        "template": "\U0001f4a5 ${notional:,.0f} liquidated on {coin} in {minutes:.0f} min "
                    "\u2014 {count} positions, {side} side, {wallets} wallets",
        "fields": {
            "notional": "cascade.burst.notional_usd  <- userFills[].px*sz (liquidation block)",
            "minutes": "cascade.burst.minutes        <- burst start/end fill time",
            "count": "cascade.burst.count            <- liquidation-flagged fills",
            "side": "cascade.burst.dominant_forced_side",
            "wallets": "cascade.burst.wallets",
        },
        "link": TRADE_PAGE,
    },
    "whale_entry": {
        "template": "\U0001f40b {short_wallet} opened ${notional:,.0f} {side} {coin} at "
                    "${price:,.2f}{leverage}",
        "fields": {
            "notional": "entries[].notional_usd  <- userFills px*sz where dir in (Open Long, Open Short)",
            "price": "entries[].price            <- userFills.px",
            "leverage": "positions[].leverage    <- clearinghouseState leverage.value (optional)",
        },
        "link": EXPLORER,
    },
    "trader_underwater": {
        "template": "\U0001f4c9 {short_wallet} down ${loss:,.0f} unrealised on {coin}: "
                    "${notional:,.0f} {side} from ${entry:,.2f} "
                    "({roe:+.1f}% ROE, liq ${liq_px:,.0f})",
        "fields": {
            "loss": "abs(positions[].unrealized_pnl) <- clearinghouseState unrealizedPnl",
            "notional": "positions[].notional_usd <- clearinghouseState positionValue",
            "entry": "positions[].entry_px        <- clearinghouseState entryPx",
            "roe": "positions[].roe_pct           <- clearinghouseState returnOnEquity",
            "liq_px": "positions[].liquidation_px <- clearinghouseState liquidationPx",
        },
        "link": EXPLORER,
    },
    "funding_flip": {
        "template": "\U0001f504 {coin} funding flipped {prev_bp:+.3f} \u2192 {now_bp:+.3f} bp/hr "
                    "({apr:+.1f}% APR), OI ${oi:,.0f}",
        "fields": {
            "prev_bp": "fundingHistory[].fundingRate  \u00d7 1e4 (previous hour)",
            "now_bp": "metaAndAssetCtxs[].funding     \u00d7 1e4 (current)",
            "apr": "fundingRate \u00d7 24 \u00d7 365 \u00d7 100",
            "oi": "metaAndAssetCtxs[].openInterest \u00d7 markPx",
        },
        "link": TRADE_PAGE,
    },
}


def _link(template: str, ctx: dict[str, Any]) -> str | None:
    """Format an explorer link, tolerating a missing wallet/coin in the context."""
    if "{" not in template:
        return template
    try:
        return template.format(**ctx)
    except KeyError:
        return None


def render_unit(unit_id: str, **ctx: Any) -> dict[str, Any] | None:
    """Render one unit. Returns None when a required number is missing."""
    unit = UNITS.get(unit_id)
    if unit is None:
        return None
    if ctx.get("notional") in (None, 0):
        return None
    try:
        text = unit["template"].format(**ctx)
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "unit": unit_id,
        "text": text,
        "fields": unit["fields"],
        "link": _link(unit["link"], ctx),
        "numbers_only": all(v is not None for v in ctx.values()),
    }

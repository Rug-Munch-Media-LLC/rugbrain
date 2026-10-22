"""Rank open Hyperliquid perp positions from keyless ``clearinghouseState`` blobs.

One state per wallet in, one flat position list out.  ``positionValue`` is the
notional at mark, ``leverage.value`` is the account's chosen leverage (not implied
leverage), and ``liquidationPx`` shows how much room is left before the position
becomes the next liquidation in the cascade feed.
"""

from __future__ import annotations

from typing import Any


def largest_positions(
    states: dict[str, dict[str, Any]], limit: int = 10, contract: str = "perp"
) -> list[dict[str, Any]]:
    """Return the top-N open positions by notional across the supplied wallet states."""
    out: list[dict[str, Any]] = []
    for wallet, state in states.items():
        if not isinstance(state, dict):
            continue
        account_value = float(state.get("marginSummary", {}).get("accountValue") or 0)
        for entry in state.get("assetPositions", []):
            pos = entry.get("position", {})
            szi = float(pos.get("szi") or 0)
            if szi == 0:
                continue
            notional = abs(float(pos.get("positionValue") or 0))
            entry_px = float(pos.get("entryPx") or 0)
            upnl = float(pos.get("unrealizedPnl") or 0)
            lev = pos.get("leverage") or {}
            out.append({
                "wallet": wallet,
                "coin": pos.get("coin"),
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "notional_usd": round(notional, 2),
                "leverage": lev.get("value"),
                "leverage_type": lev.get("type"),
                "entry_px": entry_px,
                "mark_px": round(entry_px + upnl / szi, 6) if szi else None,
                "unrealized_pnl": round(upnl, 2),
                "roe_pct": round(float(pos.get("returnOnEquity") or 0) * 100, 2),
                "liquidation_px": pos.get("liquidationPx"),
                "liq_distance_pct": round(
                    (float(pos["liquidationPx"]) / entry_px - 1) * 100, 2
                ) if pos.get("liquidationPx") and entry_px else None,
                "account_value": round(account_value, 2),
                "notional_over_equity": round(notional / account_value, 2) if account_value else None,
                "contract": contract,
            })
    out.sort(key=lambda p: -p["notional_usd"])
    return out[:limit]

"""Detect fresh directional bets from keyless ``userFills`` payloads.

``clearinghouseState`` shows what a wallet holds now; only fills show when it got
there.  A position opened inside the window with notional above a floor is a
"whale opened $X long" fact rather than an inference.
"""

from __future__ import annotations

from typing import Any

OPEN_DIRS = ("Open Long", "Open Short")


def recent_entries(
    fills: list[dict[str, Any]], since_ms: int, min_notional_usd: float = 250_000
) -> list[dict[str, Any]]:
    """Return position-opening fills newer than ``since_ms`` above the notional floor."""
    out: list[dict[str, Any]] = []
    for fill in fills:
        if fill.get("dir") not in OPEN_DIRS or fill["time"] < since_ms:
            continue
        px, sz = float(fill["px"]), float(fill["sz"])
        notional = px * sz
        if notional < min_notional_usd:
            continue
        out.append({
            "wallet": fill.get("user"),
            "coin": fill["coin"],
            "side": "long" if fill["dir"] == "Open Long" else "short",
            "price": px,
            "size": sz,
            "notional_usd": round(notional, 2),
            "fee": float(fill.get("fee") or 0),
            "crossed": fill.get("crossed"),
            "time": fill["time"],
            "tid": fill.get("tid"),
        })
    out.sort(key=lambda r: -r["notional_usd"])
    return out

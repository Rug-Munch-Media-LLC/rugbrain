"""Liquidation attribution from keyless Hyperliquid ``userFills`` payloads.

A forced fill is the only place the protocol states a liquidation happened: the
fill carries ``liquidation: {liquidatedUser, markPx, method}``.  The aggregate
``trades`` feed does **not** carry it, so a liquidation can only be claimed when
one of the two wallets' fills was read.  ``reporter == liquidatedUser`` means we
read the victim's own book; otherwise we read the counterparty and must flip the
side to describe what was forced on the victim.
"""

from __future__ import annotations

from typing import Any


def extract_liquidations(
    fills: list[dict[str, Any]], start_ms: int, end_ms: int, reporter: str
) -> list[dict[str, Any]]:
    """Return normalised liquidation records whose fill time is inside the window."""
    out: list[dict[str, Any]] = []
    for fill in fills:
        block = fill.get("liquidation")
        if not block or not (start_ms <= fill["time"] <= end_ms):
            continue
        px, sz = float(fill["px"]), float(fill["sz"])
        victim = block["liquidatedUser"]
        forced_side = fill["side"] if victim.lower() == reporter.lower() else (
            "A" if fill["side"] == "B" else "B"
        )
        mark = float(block["markPx"]) if block.get("markPx") else None
        out.append({
            "kind": "liquidation",
            "wallet": victim,
            "observed_via": reporter,
            "coin": fill["coin"],
            "forced_side": forced_side,          # A = victim sold (was long), B = bought (was short)
            "price": px,
            "size": sz,
            "notional_usd": round(px * sz, 2),
            "mark_px": mark,
            "mark_slip_bps": round((px / mark - 1) * 10_000, 2) if mark else None,
            "method": block.get("method"),
            "time": fill["time"],
            "tid": fill["tid"],
            "hash": fill.get("hash"),
        })
    return out

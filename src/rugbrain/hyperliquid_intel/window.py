"""Summarise a captured Hyperliquid trade window (JSONL rows -> stats dict).

Input rows are what the candidate ingester emits: one ``kind=trade`` record per
WS fill plus periodic ``kind=mark`` snapshots.  Everything here is pure — no
network, no clock — so the same function serves a live buffer and a fixture.
"""

from __future__ import annotations

from typing import Any

COINS: tuple[str, ...] = ("BTC", "ETH", "SOL")


def summarise_window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return window totals, per-coin moves, top-10 notional trades and feed lag."""
    trades = sorted((r for r in rows if r.get("kind") == "trade"), key=lambda r: r["time"])
    marks = [r for r in rows if r.get("kind") == "mark"]
    if not trades:
        return {"window": {"trades": 0}, "per_coin": {}, "top_trades": [], "lag_ms": {}}

    start, end = trades[0]["time"], trades[-1]["time"]
    lags = sorted(r["ts_ingest"] - r["time"] for r in trades)
    per_coin: dict[str, Any] = {}
    for coin in COINS:
        xs = [t for t in trades if t["coin"] == coin]
        if not xs:
            continue
        px = [t["px"] for t in xs]
        oracle = [float(m["oraclePx"]) for m in marks if m["coin"] == coin and m.get("oraclePx")]
        per_coin[coin] = {
            "trades": len(xs),
            "notional_usd": round(sum(t["notional_usd"] for t in xs), 2),
            "buy_notional_usd": round(sum(t["notional_usd"] for t in xs if t["side"] == "B"), 2),
            "sell_notional_usd": round(sum(t["notional_usd"] for t in xs if t["side"] == "A"), 2),
            "first_px": px[0], "last_px": px[-1],
            "low_px": min(px), "high_px": max(px),
            "pct_move": round((px[-1] / px[0] - 1) * 100, 3),
            "oracle_first": oracle[0] if oracle else None,
            "oracle_last": oracle[-1] if oracle else None,
            "oracle_pct": round((oracle[-1] / oracle[0] - 1) * 100, 3) if len(oracle) > 1 else None,
        }

    top = sorted(trades, key=lambda t: -t["notional_usd"])[:10]
    return {
        "window": {
            "start_ms": start, "end_ms": end,
            "minutes": round((end - start) / 60_000, 1),
            "trades": len(trades),
            "total_notional_usd": round(sum(t["notional_usd"] for t in trades), 2),
            "distinct_wallets": len({u for t in trades for u in (t.get("users") or [])}),
        },
        "lag_ms": {"p50": lags[len(lags) // 2], "p95": lags[int(len(lags) * 0.95)], "max": lags[-1]},
        "per_coin": per_coin,
        "top_trades": [
            {k: t.get(k) for k in ("coin", "side", "px", "sz", "notional_usd", "time",
                                   "users", "tid")}
            for t in top
        ],
    }

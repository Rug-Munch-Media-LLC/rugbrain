"""Turn individual liquidation fills into a cascade statement with a clock.

A cascade is a burst, not a total: the unit of evidence is "N liquidations worth
$X inside M minutes", so the burst window is derived from the fills themselves
and the biggest single burst is reported alongside the whole-window total.
"""

from __future__ import annotations

from typing import Any

BURST_GAP_MS = 120_000


def summarise_cascade(liqs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return totals per coin plus the densest burst (gap-chained, >=2 events)."""
    if not liqs:
        return {"count": 0, "notional_usd": 0.0, "burst": None, "per_coin": {}}

    ordered = sorted(liqs, key=lambda r: r["time"])
    per_coin: dict[str, Any] = {}
    for row in ordered:
        acc = per_coin.setdefault(row["coin"], {"count": 0, "notional_usd": 0.0,
                                                "sold_usd": 0.0, "bought_usd": 0.0})
        acc["count"] += 1
        acc["notional_usd"] = round(acc["notional_usd"] + row["notional_usd"], 2)
        key = "sold_usd" if row["forced_side"] == "A" else "bought_usd"
        acc[key] = round(acc[key] + row["notional_usd"], 2)

    bursts: list[list[dict[str, Any]]] = [[ordered[0]]]
    for row in ordered[1:]:
        if row["time"] - bursts[-1][-1]["time"] <= BURST_GAP_MS:
            bursts[-1].append(row)
        else:
            bursts.append([row])
    best = max(bursts, key=lambda b: sum(r["notional_usd"] for r in b))
    burst = None
    if len(best) >= 2:
        burst = {
            "coin": max({r["coin"] for r in best}, key=lambda c: sum(
                r["notional_usd"] for r in best if r["coin"] == c)),
            "count": len(best),
            "notional_usd": round(sum(r["notional_usd"] for r in best), 2),
            "start_ms": best[0]["time"],
            "end_ms": best[-1]["time"],
            "minutes": round((best[-1]["time"] - best[0]["time"]) / 60_000, 1),
            "dominant_forced_side": "A" if sum(
                r["notional_usd"] for r in best if r["forced_side"] == "A") > sum(
                r["notional_usd"] for r in best if r["forced_side"] == "B") else "B",
            "wallets": len({r["wallet"] for r in best}),
        }
    return {
        "count": len(ordered),
        "notional_usd": round(sum(r["notional_usd"] for r in ordered), 2),
        "wallets": len({r["wallet"] for r in ordered}),
        "per_coin": per_coin,
        "burst": burst,
    }

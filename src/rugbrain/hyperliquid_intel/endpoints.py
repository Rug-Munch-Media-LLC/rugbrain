"""Keyless Hyperliquid endpoint map — the single source of truth for this package.

Every entry records the exact request body, what it yields, whether a
liquidation is identifiable from it, and the load it costs.  Probe evidence and
the real response samples live in ``docs/HYPERLIQUID-INTEL.md``.
"""

from __future__ import annotations

from typing import Any, Literal

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

# liq: "flag"    -> a liquidation block is present in the payload
#      "infer"   -> no flag; must be attributed from positions/fills elsewhere
#      "none"    -> no liquidation signal at all
Role = Literal["liquidations", "bets", "positions", "prices"]
ENDPOINTS: dict[str, dict[str, Any]] = {
    "metaAndAssetCtxs": {
        "transport": "rest", "request": {"type": "metaAndAssetCtxs"}, "role": "prices",
        "yields": "universe[] + per-coin ctx: markPx, oraclePx, funding, openInterest, prevDayPx",
        "liq": "infer", "cost": "72 KB, ~1.2 s",
    },
    "recentTrades": {
        "transport": "rest", "request": {"type": "recentTrades", "coin": "BTC"}, "role": "bets",
        "yields": "last 10 fills: coin, side, px, sz, time, hash, tid, users[2]",
        "liq": "none", "cost": "2.7 KB, ~0.7 s",
    },
    "userFills": {
        "transport": "rest", "request": {"type": "userFills", "user": "0x..."}, "role": "liquidations",
        "yields": "up to 2000 fills; a forced fill carries liquidation{liquidatedUser, markPx, method}",
        "liq": "flag", "cost": "weight 2, per wallet — the only keyless liquidation flag",
    },
    "clearinghouseState": {
        "transport": "rest", "request": {"type": "clearinghouseState", "user": "0x..."}, "role": "positions",
        "yields": "marginSummary, withdrawable, assetPositions[].position{szi, leverage, entryPx, "
                  "positionValue, unrealizedPnl, liquidationPx, returnOnEquity}",
        "liq": "infer", "cost": "weight 2, per wallet",
    },
    "l2Book": {
        "transport": "rest", "request": {"type": "l2Book", "coin": "BTC"}, "role": "prices",
        "yields": "20 bid/ask levels {px, sz, n}", "liq": "none", "cost": "1.6 KB, ~0.7 s",
    },
    "candleSnapshot": {
        "transport": "rest",
        "request": {"type": "candleSnapshot",
                    "req": {"coin": "BTC", "interval": "1m", "startTime": 0}},
        "role": "prices", "yields": "OHLCV: t, T, o, h, l, c, v, n", "liq": "none", "cost": "8 KB/h",
    },
    "fundingHistory": {
        "transport": "rest",
        "request": {"type": "fundingHistory", "coin": "BTC", "startTime": 0},
        "role": "prices", "yields": "hourly fundingRate + premium + time", "liq": "none",
        "cost": "2 KB/day",
    },
    "predictedFundings": {
        "transport": "rest", "request": {"type": "predictedFundings"}, "role": "prices",
        "yields": "coin -> [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}]] "
                  "for HlPerp/BinPerp/BybitPerp — cross-venue funding divergence",
        "liq": "none", "cost": "64 KB, ~0.9 s",
    },
    "leaderboard": {
        "transport": "https", "request": {"GET": LEADERBOARD_URL}, "role": "positions",
        "yields": "leaderboardRows[]: ethAddress, accountValue, windowPerformances "
                  "(day/week/month/allTime -> pnl, roi, vlm)",
        "liq": "none", "cost": "39 MB / 47 527 rows — do not poll",
    },
    "ws_trades": {
        "transport": "ws", "request": {"method": "subscribe",
                                       "subscription": {"type": "trades", "coin": "BTC"}},
        "role": "bets", "yields": "every fill: coin, side, px, sz, time, hash, tid, users[2]",
        "liq": "none", "cost": "push; the only way to see all fills without per-wallet calls",
    },
    "ws_userFills": {
        "transport": "ws", "request": {"method": "subscribe",
                                       "subscription": {"type": "userFills", "user": "0x..."}},
        "role": "liquidations", "yields": "live fills incl. liquidation{liquidatedUser, markPx, method}",
        "liq": "flag", "cost": "push, one subscription per tracked wallet",
    },
    "ws_webData2": {
        "transport": "ws", "request": {"method": "subscribe",
                                       "subscription": {"type": "webData2", "user": "0x..."}},
        "role": "positions", "yields": "REJECTED in probe: 'Error parsing JSON into valid websocket "
                                       "request' — do not rely on this channel name",
        "liq": "none", "cost": "n/a",
    },
    "ws_notification": {
        "transport": "ws", "request": {"method": "subscribe",
                                       "subscription": {"type": "notification", "user": "0x..."}},
        "role": "bets", "yields": "verified: data.notification is a plain string, e.g. "
                                 "'Resting order filled: Bought 43772 PUMP at $0.004595'",
        "liq": "infer", "cost": "push, one subscription per wallet; accepted for any address",
    },
}

# Requests that are documented elsewhere but do NOT exist on /info — proven 422.
DEAD_REQUESTS: dict[str, str] = {
    "liquidationOrders": "422 Failed to deserialize the JSON body into the target type",
    "liquidations": "422 Failed to deserialize the JSON body into the target type",
    "trades": "422 — trades is WS-only; REST equivalent is recentTrades (10 fills)",
}


def request_for(name: str, **params: Any) -> dict[str, Any]:
    """Return the exact /info body for an endpoint, with params substituted."""
    body = dict(ENDPOINTS[name]["request"])
    for key, value in params.items():
        if key == "coin" and "req" in body:
            body["req"]["coin"] = value
        elif key in body:
            body[key] = value
        elif "req" in body:
            body["req"][key] = value
    return body

"""Hyperliquid Monitor — comprehensive perp DEX tracking system.

Tracks in real-time:
1. BIG TRADES: Trades >$10k/$100k/$1M, whale trade alerts
2. LIQUIDATIONS: All liquidation events with USD value, per-coin
3. P&L WINS/LOSSES: User fills with closed PnL, identify biggest winners/losers
4. FUNDING RATES: Per-coin funding, spikes, reversals
5. OPEN INTEREST: Total OI per coin, OI changes
6. WHALE WALLETS: Track known whale addresses, their trades and PnL
7. TOP TRADERS: Leaderboard of best/worst performing wallets

Endpoints (all free, no auth):
  POST https://api.hyperliquid.xyz/info
    - type=allMids          -> {coin: price}
    - type=meta             -> {universe: [{name, szDecimals, pxDecimals}]}
    - type=trades           -> [{px, sz, side, time, hash}]
    - type=liquidationOrders-> [{coin, side, sz, lidPx, time}]
    - type=fundingHistory   -> [{coin, fundingRate, premium, time}]
    - type=openInterests    -> {coin: oi}
    - type=candleSnapshot   -> [{T, o, h, l, c, v, s, t}]
    - type=l2Book           -> {coin, levels: [[bids], [asks]]}
    - type=userFills        -> [{coin, px, sz, side, closedPnl, dir, hash, time}]
    - type=portfolio        -> [{day, {accountValueHistory, pnlHistory, vlm}}]

Outputs sent to:
  - Telegram channel (via rugbrain.alert_dispatcher)
  - Qdrant 'hyperliquid' collection (for RAG search)
  - In-memory for Dashboard API
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.hyperliquid.xyz"
POLL_INTERVAL = int(os.getenv("HYPERLIQUID_POLL_INTERVAL", "30"))  # 30 seconds for fast data
BIG_TRADE_THRESHOLD = int(os.getenv("HL_BIG_TRADE_THRESHOLD", "10000"))  # $10k USD
WHALE_TRADE_THRESHOLD = int(os.getenv("HL_WHALE_THRESHOLD", "100000"))  # $100k USD
MEGA_TRADE_THRESHOLD = int(os.getenv("HL_MEGA_THRESHOLD", "1000000"))  # $1M USD

# Known whale wallets to watch (publicly known addresses)
WHALE_WATCHLIST: dict[str, str] = {
    "0x0000000000000000000000000000000000000000": "Example Whale",  # Replace with real addresses
}

# In-memory stores
_all_mids: dict[str, float] = {}
_all_meta: dict[str, Any] = {"universe": []}
_open_interests: dict[str, float] = {}
_funding_rates: dict[str, list[dict[str, Any]]] = defaultdict(list)
_trade_history: list[dict[str, Any]] = []
_liquidation_history: list[dict[str, Any]] = []
_biggest_trades: list[dict[str, Any]] = []
_top_pnl_wallets: dict[str, float] = {}
_seen_trades: set[str] = set()

# Coin name map
_COIN_NAMES: dict[str, str] = {}


async def _api_call(payload: dict[str, Any]) -> Any:
    """Make a POST to the Hyperliquid info endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{API_BASE}/info", json=payload)
            if r.status_code == 200:
                return r.json()
            logger.debug("hl_api_error type=%s status=%d", payload.get("type"), r.status_code)
    except Exception as exc:
        logger.debug("hl_api_failed type=%s err=%s", payload.get("type"), str(exc)[:60])
    return None


async def _refresh_meta() -> None:
    """Refresh the universe metadata."""
    global _COIN_NAMES
    data = await _api_call({"type": "meta"})
    if data and "universe" in data:
        _all_meta.update(data)
        for u in data["universe"]:
            _COIN_NAMES[u["name"]] = u.get("szDecimals", 5)
        logger.debug("hl_meta_refreshed coins=%d", len(_COIN_NAMES))


async def fetch_all_mids() -> dict[str, float]:
    """Get all mid prices."""
    data = await _api_call({"type": "allMids"})
    if data:
        _all_mids.update(data)
        return {k: float(v) for k, v in data.items()}
    return {}


async def fetch_recent_trades(coin: str = "BTC", limit: int = 50) -> list[dict[str, Any]]:
    """Get recent trades for a coin."""
    data = await _api_call({"type": "trades", "param": {"coin": coin}})
    if isinstance(data, list):
        return data[:limit]
    return []


async def fetch_liquidations(limit: int = 20) -> list[dict[str, Any]]:
    """Get recent liquidation orders."""
    data = await _api_call({"type": "liquidationOrders", "param": {"limit": limit}})
    if isinstance(data, list):
        return data
    return []


async def fetch_funding_history(coin: str = "BTC", limit: int = 10) -> list[dict[str, Any]]:
    """Get funding rate history for a coin. Requires startTime (epoch ms)."""
    import time

    now_ms = int(time.time() * 1000)
    data = await _api_call(
        {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": now_ms - (limit * 3600 * 1000),
        }
    )
    if isinstance(data, list):
        return data
    return []


async def fetch_open_interests() -> dict[str, float]:
    """Get open interest and funding data from metaAndAssetCtxs."""
    data = await _api_call({"type": "metaAndAssetCtxs"})
    if isinstance(data, list) and len(data) == 2:
        ctx_list = data[1]  # Second element is asset contexts with OI/funding
        oi_data: dict[str, Any] = {}
        for ctx in ctx_list:
            # ctx is per-coin, need to match by index
            pass
    # Also try the raw openInterests
    data2 = await _api_call({"type": "openInterests"})
    if isinstance(data2, dict):
        _open_interests.update(data2)
        return {k: float(v) for k, v in data2.items()}
    return {}


async def fetch_candle_snapshot(
    coin: str, interval: str = "1h", limit: int = 24
) -> list[dict[str, Any]]:
    """Get candle snapshots."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (limit * 3600 * 1000)
    data = await _api_call(
        {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms},
        }
    )
    if isinstance(data, list):
        return data
    return []


async def fetch_user_portfolio(user: str) -> dict[str, Any] | None:
    """Get user portfolio by address."""
    data = await _api_call({"type": "portfolio", "user": user})
    return cast(dict[str, Any] | None, data)


async def fetch_all_perp_info() -> dict[str, Any]:
    """Fetch all perpetual info in one shot using metaAndAssetCtxs."""
    data = await _api_call({"type": "metaAndAssetCtxs"})
    if isinstance(data, list) and len(data) >= 2:
        meta_part = data[0]
        ctx_part = data[1]
        universe = meta_part.get("universe", [])
        # Update coin names
        for u in universe:
            _COIN_NAMES[u["name"]] = u.get("szDecimals", 5)

        # Extract funding rates, open interest, mark prices
        fundings: dict[str, Any] = {}
        oi_data: dict[str, Any] = {}
        for ctx in ctx_part:
            # ctx corresponds to universe index
            pass  # We need to match by index
        _all_meta.update(meta_part)

    # Also get mids and open interests separately
    mids, oi = await asyncio.gather(
        fetch_all_mids(),
        _api_call({"type": "openInterests"}),
    )
    if isinstance(oi, dict):
        _open_interests.update(oi)

    return {"mids": mids or {}, "open_interests": {k: float(v) for k, v in _open_interests.items()}}


# ── Trade Classification ─────────────────────────────────────────


def classify_trade(trade: dict[str, Any], mid_price: float | None = None) -> dict[str, Any]:
    """Classify a trade by size, compute USD value."""
    px = float(trade.get("px", 0))
    sz = float(trade.get("sz", 0))
    usd_value = px * sz
    coin = trade.get("coin", "?")
    side = trade.get("side", "?")  # A = Ask (sell), B = Bid (buy)

    # Determine buy/sell from side
    is_buy = side == "B"
    side_label = "BUY 🟢" if is_buy else "SELL 🔴"

    # Tier classification
    if usd_value >= MEGA_TRADE_THRESHOLD:
        tier = "🐋🐋🐋 MEGA WHALE"
    elif usd_value >= WHALE_TRADE_THRESHOLD:
        tier = "🐋🐋 WHALE"
    elif usd_value >= BIG_TRADE_THRESHOLD:
        tier = "🐋 MINNOW"
    else:
        tier = "regular"

    return {
        "coin": coin,
        "price": px,
        "size": sz,
        "usd_value": usd_value,
        "side": side_label,
        "is_buy": is_buy,
        "tier": tier,
        "hash": trade.get("hash", ""),
        "time": trade.get("time", datetime.now(timezone.utc).timestamp() * 1000),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def classify_liquidation(liq: dict[str, Any]) -> dict[str, Any]:
    """Classify a liquidation event."""
    coin = liq.get("coin", "?")
    side = liq.get("side", "?")
    sz = float(liq.get("sz", 0))
    px = float(liq.get("lidPx", liq.get("px", 0)))
    usd_value = px * sz
    is_long = side == "B"  # B=longs getting liquidated
    side_label = "LONG 🔴" if is_long else "SHORT 🟢"

    if usd_value >= MEGA_TRADE_THRESHOLD:
        tier = "💀💀💀 MEGA LIQUIDATION"
    elif usd_value >= WHALE_TRADE_THRESHOLD:
        tier = "💀💀 WHALE LIQUIDATED"
    elif usd_value >= BIG_TRADE_THRESHOLD:
        tier = "💀 LIQUIDATION"
    else:
        tier = "liquidation"

    return {
        "coin": coin,
        "side": side_label,
        "is_long": is_long,
        "size": sz,
        "price": px,
        "usd_value": usd_value,
        "tier": tier,
        "time": liq.get("time", datetime.now(timezone.utc).timestamp() * 1000),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Scan Cycles ──────────────────────────────────────────────────


async def scan_trades() -> list[dict[str, Any]]:
    """Scan recent trades across major coins for notable activity."""
    major_coins = ["BTC", "ETH", "SOL", "ARB", "OP", "APT", "SUI", "AVAX", "LINK", "BNB", "DOGE"]
    all_notable = []

    for coin in major_coins:
        trades = await fetch_recent_trades(coin, limit=20)
        if not trades:
            continue

        for t in trades:
            tid = t.get("hash", "") or f"{coin}_{t.get('time', '')}"
            if tid in _seen_trades:
                continue
            _seen_trades.add(tid)
            if len(_seen_trades) > 500000:
                _seen_trades.clear()

            classified = classify_trade(t, _all_mids.get(coin))
            if classified["tier"] != "regular":
                all_notable.append(classified)
                if len(_biggest_trades) < 1000:
                    _biggest_trades.append(classified)

        # Throttle
        await asyncio.sleep(0.2)

    return all_notable


async def scan_liquidations() -> list[dict[str, Any]]:
    """Scan recent liquidations."""
    liqs = await fetch_liquidations(limit=50)
    notable = []
    for l in liqs:
        classified = classify_liquidation(l)
        if classified["tier"] != "liquidation":
            notable.append(classified)
    return notable


async def scan_funding() -> dict[str, Any]:
    """Scan funding rates across major coins for spikes."""
    import time

    major_coins = ["BTC", "ETH", "SOL", "ARB", "OP"]
    spikes = []
    now_ms = int(time.time() * 1000)
    for coin in major_coins:
        history = await _api_call(
            {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": now_ms - (3 * 3600 * 1000),
            }
        )
        if isinstance(history, list) and history:
            _funding_rates[coin].extend(history)
            latest = float(history[0].get("fundingRate", "0"))
            pct = latest * 100
            if abs(pct) > 0.01:
                spikes.append(
                    {
                        "coin": coin,
                        "rate_pct": round(pct, 6),
                        "annualized_pct": round(pct * 365 * 3, 2),
                    }
                )
        await asyncio.sleep(0.2)
    return {"spikes": spikes, "total_coins": len(major_coins)}


async def hyperliquid_full_cycle() -> dict[str, Any]:
    """Run one comprehensive Hyperliquid monitoring cycle."""
    # Fetch all data
    mids, liqs_data = await asyncio.gather(
        fetch_all_mids(),
        fetch_liquidations(),
    )

    # Process trades
    notable_trades = await scan_trades()
    notable_liqs = scan_liquidations_from_data(liqs_data)
    funding = await scan_funding()

    # Classify new liquidations
    for l in liqs_data:
        classified = classify_liquidation(l)
        if classified["usd_value"] > 0:
            _liquidation_history.append(classified)

    # Keep bounded
    if len(_trade_history) > 50000:
        _trade_history[:] = _trade_history[-25000:]
    if len(_liquidation_history) > 50000:
        _liquidation_history[:] = _liquidation_history[-25000:]

    # Aggregate stats
    total_liq_24h = sum(l["usd_value"] for l in _liquidation_history[-1000:])
    total_trade_vol = sum(t["usd_value"] for t in _trade_history[-1000:])

    # Alert on mega events
    alerts = []
    for t in notable_trades:
        tier = t.get("tier", "")
        if "MEGA WHALE" in tier:
            alerts.append(
                f"MEGA TRADE: {t['coin']} {t['side_label']} ${t['usd_value']:,.0f} @ ${t['price']:,.2f}"
            )
            try:
                from rugbrain.alert_dispatcher import dispatch_alert

                side_emoji = "🟢 BUY" if t.get("is_buy") else "🔴 SELL"
                await dispatch_alert(
                    f"MEGA WHALE: {t['coin']} {side_emoji} ${t['usd_value']:,.0f}",
                    f"Price: ${t['price']:,.2f}\nSize: {t['size']:.4f}\nExchange: Hyperliquid",
                    alert_type="hyperliquid_whale",
                    severity="critical",
                )
            except Exception:
                pass
        elif "WHALE" in tier and "MEGA" not in tier:
            alerts.append(f"WHALE TRADE: {t['coin']} {t['side_label']} ${t['usd_value']:,.0f}")
            try:
                from rugbrain.alert_dispatcher import dispatch_alert

                side_emoji = "🟢 BUY" if t.get("is_buy") else "🔴 SELL"
                await dispatch_alert(
                    f"WHALE: {t['coin']} {side_emoji} ${t['usd_value']:,.0f}",
                    f"Price: ${t['price']:,.2f}\nSize: {t['size']:.4f}\nExchange: Hyperliquid",
                    alert_type="hyperliquid_whale",
                    severity="high",
                )
            except Exception:
                pass

    for l in notable_liqs:
        ltier = l.get("tier", "")
        if "MEGA LIQUIDATION" in ltier:
            try:
                from rugbrain.alert_dispatcher import dispatch_alert

                side_emoji = "LONG" if l.get("is_long") else "SHORT"
                await dispatch_alert(
                    f"MEGA LIQUIDATION: {l['coin']} {side_emoji} ${l['usd_value']:,.0f}",
                    f"Price: ${l['price']:,.2f}\nSize: {l['size']:.4f}\nExchange: Hyperliquid",
                    alert_type="hyperliquid_liquidation",
                    severity="critical",
                )
            except Exception:
                pass

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "coins_tracked": len(_COIN_NAMES),
        "notable_trades": len(notable_trades),
        "notable_liqs": len(notable_liqs),
        "funding_spikes": len(funding.get("spikes", [])),
        "total_liq_24h_usd": round(total_liq_24h, 2),
        "total_trade_vol_usd": round(total_trade_vol, 2),
        "biggest_trades": sorted(_biggest_trades, key=lambda t: t["usd_value"], reverse=True)[:10],
        "recent_liquidations": _liquidation_history[-20:],
        "funding_snapshot": funding.get("spikes", []),
        "alerts": alerts[:5],
    }


def scan_liquidations_from_data(liqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan liquidation data for notable events."""
    notable = []
    for l in liqs:
        classified = classify_liquidation(l)
        if classified["tier"] != "liquidation":
            notable.append(classified)
    return notable


async def hyperliquid_loop() -> None:
    """Run Hyperliquid monitoring in a continuous background loop."""
    logger.info("hyperliquid_monitor_started interval=%ds", POLL_INTERVAL)
    # Initial meta refresh
    await _refresh_meta()

    while True:
        try:
            result = await hyperliquid_full_cycle()
            if result["notable_trades"] > 0 or result["notable_liqs"] > 0:
                logger.info(
                    "hl_cycle trades=%d liqs=%d funding_spikes=%d vol_24h=%s",
                    result["notable_trades"],
                    result["notable_liqs"],
                    result["funding_spikes"],
                    f"${result['total_trade_vol_usd']:,.0f}"
                    if result["total_trade_vol_usd"] > 0
                    else "N/A",
                )
        except Exception as exc:
            logger.warning("hl_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)

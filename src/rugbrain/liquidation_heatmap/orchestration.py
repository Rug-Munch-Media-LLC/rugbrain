"""Orchestration for liquidation monitoring cycles."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from rugbrain.liquidation_heatmap.cex_fetchers import (
    get_binance_liquidations,
    get_bitmex_liquidations,
    get_bybit_liquidations,
    get_deribit_liquidations,
    get_hyperliquid_liquidations,
    get_kraken_futures_liquidations,
    get_kucoin_futures_liquidations,
    get_okx_liquidations,
    get_phemex_liquidations,
)
from rugbrain.liquidation_heatmap.constants import (
    POLL_INTERVAL,
    _24h_volume,
    _biggest_single,
    _liquidation_history,
    _peak_24h,
    logger,
)
from rugbrain.liquidation_heatmap.protocol_fetchers import (
    get_dydx_liquidations,
    get_gmx_liquidations,
    get_protocol_liquidations,
    get_synthetix_liquidations,
)
from rugbrain.liquidation_heatmap.analysis import (
    get_hype_liquidations,
    get_liquidation_wins,
    get_total_market_liq_24h,
)

__all__ = [
    "liquidation_scan_cycle",
    "liquidation_scan_loop",
]


async def liquidation_scan_cycle() -> dict[str, Any]:
    """Run one comprehensive liquidation monitoring cycle."""
    global _24h_volume, _peak_24h, _biggest_single

    # Gather all sources in parallel
    (
        binance,
        bybit,
        okx,
        protocols,
        dydx,
        gmx,
        synthetix,
        kraken,
        bitmex,
        deribit,
        hyperliquid,
        kucoin,
        phemex,
    ) = await asyncio.gather(
        get_binance_liquidations(),
        get_bybit_liquidations(),
        get_okx_liquidations(),
        get_protocol_liquidations(),
        get_dydx_liquidations(),
        get_gmx_liquidations(),
        get_synthetix_liquidations(),
        get_kraken_futures_liquidations(),
        get_bitmex_liquidations(),
        get_deribit_liquidations(),
        get_hyperliquid_liquidations(),
        get_kucoin_futures_liquidations(),
        get_phemex_liquidations(),
    )
    market = await get_total_market_liq_24h()

    all_events = (
        binance
        + bybit
        + okx
        + protocols
        + dydx
        + gmx
        + synthetix
        + kraken
        + bitmex
        + deribit
        + hyperliquid
        + kucoin
        + phemex
    )
    _liquidation_history.extend(all_events)
    if len(_liquidation_history) > 50000:
        _liquidation_history[:] = _liquidation_history[-25000:]

    # Update 24h volume
    if market.get("estimated_total_24h", 0) > 0:
        _24h_volume = market["estimated_total_24h"]
        if _24h_volume > _peak_24h:
            _peak_24h = _24h_volume

    # Find biggest single liquidation
    for e in all_events:
        if e.get("value_usd", 0) > _biggest_single.get("value_usd", 0):
            _biggest_single = e

    # Aggregate by exchange
    by_exchange: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "volume_usd": 0.0}
    )
    for e in all_events:
        ex = e.get("exchange", "unknown")
        by_exchange[ex]["count"] += 1
        by_exchange[ex]["volume_usd"] += e.get("value_usd", 0)

    # Hype + wins
    hype = get_hype_liquidations(all_events)
    wins = get_liquidation_wins(all_events)

    # 24h aggregation
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)
    last_24h = [
        e
        for e in _liquidation_history
        if e.get("timestamp", now.isoformat()) >= cutoff_24h.isoformat()
    ]
    vol_24h = float(sum(e.get("value_usd", 0) for e in last_24h))

    # Alert on mega liquidations
    for e in all_events:
        if e.get("value_usd", 0) > 10_000_000:
            try:
                from rugbrain.alert_dispatcher import dispatch_alert

                asyncio.ensure_future(  # noqa: RUF006 — pre-existing, audited 2026-08-22
                    dispatch_alert(
                        f"MEGA LIQUIDATION: ${e['value_usd']:,.0f} on {e.get('exchange', '?')}",
                        f"Symbol: {e.get('symbol', '?')}\nPrice: ${e.get('price', 0)}\nSeverity: CRITICAL",
                        alert_type="liquidation",
                        severity="critical",
                    )
                )
            except Exception:
                pass

    return {
        "scanned_at": now.isoformat(),
        "total_events": len(all_events),
        "total_liquidations_24h": len(last_24h),
        "volume_24h_usd": round(vol_24h, 2),
        "volume_24h_label": f"${vol_24h:,.0f}",
        "estimated_market_24h": market,
        "by_exchange": {
            k: {"count": v["count"], "volume_usd": round(v["volume_usd"], 2)}
            for k, v in by_exchange.items()
        },
        "hype_liquidations": hype[:10],
        "liquidation_wins": wins[:10],
        "biggest_single": _biggest_single,
        "peak_24h": round(_peak_24h, 2),
        "alerts": len([e for e in all_events if e.get("value_usd", 0) > 1_000_000]),
    }


async def liquidation_scan_loop() -> None:
    """Run liquidation monitoring in a continuous background loop."""
    logger.info("liquidation_heatmap_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await liquidation_scan_cycle()
            if result["total_events"] > 0:
                logger.info(
                    "liq_cycle events=%d vol_24h=%s exchanges=%d alerts=%d",
                    result["total_events"],
                    result["volume_24h_label"],
                    len(result["by_exchange"]),
                    result["alerts"],
                )
        except Exception as exc:
            logger.warning("liq_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)

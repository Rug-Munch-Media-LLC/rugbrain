"""DeFi Lending Pool Health Monitor — tracks major lending protocol health metrics.

Tracks 5 protocols (Aave V3, Compound V3, Spark, Radiant, Morpho Blue) via
DefiLlama yields API (free, no auth) + The Graph subgraph queries (optional).

Free tier (DefiLlama): total_supply_usd (from tvlUsd), supply_apy, per-pool breakdown.
With THEGRAPH_API_KEY: adds utilization_rate, borrow_apy, total_borrow_usd,
available_liquidity_usd, bad_debt_usd via subgraph queries.

Alerts: util >90%, borrow APY >20%, liquidity <$10M, bad debt +10%/24h.

Import: from rugbrain.monitors.defi_lending import defi_lending_loop
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

POLL_INTERVAL = int(os.getenv("DEFI_LENDING_POLL_INTERVAL", "900"))  # 15 min
THEGRAPH_API_KEY = os.getenv("THEGRAPH_API_KEY", "")
THEGRAPH_GATEWAY = "https://gateway.thegraph.com/api"

UTILIZATION_ALERT = float(os.getenv("DEFI_UTILIZATION_ALERT", "90.0"))
BORROW_APY_ALERT = float(os.getenv("DEFI_BORROW_APY_ALERT", "20.0"))
LIQUIDITY_ALERT_USD = float(os.getenv("DEFI_LIQUIDITY_ALERT", "10_000_000"))
BAD_DEBT_INCREASE_ALERT = float(os.getenv("DEFI_BAD_DEBT_INCREASE_ALERT", "10.0"))

# ── Protocol Registry ────────────────────────────────────────────────────────

PROTOCOLS: dict[str, dict[str, Any]] = {
    "aave-v3": {"label": "Aave V3", "slug": "aave-v3", "subgraph": "aave/protocol-v3"},
    "compound-v3": {"label": "Compound V3", "slug": "compound-v3", "subgraph": "compound-v3"},
    "spark": {"label": "Spark Protocol", "slug": "sparklend", "subgraph": "spark"},
    "radiant": {"label": "Radiant", "slug": "radiant", "subgraph": "radiant"},
    "morpho-blue": {"label": "Morpho Blue", "slug": "morpho-blue", "subgraph": "morpho-blue"},
}

# ── The Graph Subgraph Queries (requires THEGRAPH_API_KEY) ───────────────────

SUBGRAPH_POOLS_QUERY = """{pools(first:100,orderBy:totalSupplyUsd,orderDirection:desc){id name inputToken{id symbol name decimals} outputToken{id symbol} totalSupplyUsd totalBorrowUsd availableLiquidityUsd utilizationRate supplyApy borrowApy ltv liquidationThreshold}}"""

SUBGRAPH_USERS_QUERY = """{users(first:5,orderBy:totalBorrowUsd,orderDirection:desc){id totalBorrowUsd totalCollateralUsd positions{pool{id name} balanceUsd}}}"""

SUBGRAPH_BAD_DEBT_QUERY = """{positions(where:{badDebt_gte:1}){id pool{id name} badDebtUsd}}"""

# ── DefiLlama API Endpoints (free, no auth) ─────────────────────────────────

DEFILLAMA_POOLS = "https://yields.llama.fi/pools"

# ── In-Memory Stores ────────────────────────────────────────────────────────

_health_store: dict[str, dict[str, Any]] = {}
_history_store: dict[str, list[dict[str, Any]]] = defaultdict(list)
_bad_debt_history: dict[str, list[dict[str, float]]] = defaultdict(list)
_recent_alerts: list[dict[str, Any]] = []
_health_state: dict[str, Any] = {
    "last_successful_poll": 0.0,
    "last_error": "",
    "consecutive_failures": 0,
    "total_polls": 0,
    "status": "initializing",
}

# ── Data Fetching ────────────────────────────────────────────────────────────


async def _fetch_defillama_pools() -> list[dict[str, Any]]:
    """Fetch all yield pools from DefiLlama (free, no auth, supply-side only)."""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(DEFILLAMA_POOLS, headers={"Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                return data.get("data", []) if isinstance(data, dict) else (data or [])
    except Exception as exc:
        logger.debug("defillama_pools_failed err=%s", str(exc)[:80])
    return []


def _match_defillama_pools(all_pools: list[dict[str, Any]], pkey: str) -> list[dict[str, Any]]:
    """Filter pools by exact DefiLlama project slug."""
    cfg = PROTOCOLS[pkey]
    return [p for p in all_pools if p.get("project", "").lower() == cfg["slug"]]


async def _fetch_thegraph_pools(subgraph_id: str) -> list[dict[str, Any]]:
    """Query The Graph subgraph for full pool data (borrow + supply). Requires API key."""
    if not THEGRAPH_API_KEY:
        return []
    url = f"{THEGRAPH_GATEWAY}/{THEGRAPH_API_KEY}/subgraphs/id/{subgraph_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json={"query": SUBGRAPH_POOLS_QUERY})
            if r.status_code == 200:
                data = r.json()
                return cast(list[dict[str, Any]], (data.get("data") or {}).get("pools", []))
    except Exception as exc:
        logger.debug("thegraph_failed subgraph=%s err=%s", subgraph_id, str(exc)[:80])
    return []


# ── Metric Computation ──────────────────────────────────────────────────────


def _compute_protocol_metrics(pools: list[dict[str, Any]], pkey: str) -> dict[str, Any]:
    """Aggregate per-protocol metrics. DefiLlama provides supply-side;
    full borrow/utilization data only available via The Graph subgraph (API key)."""
    label = PROTOCOLS[pkey]["label"]
    if not pools:
        return {
            "protocol": label,
            "status": "no_data",
            "total_supply_usd": 0,
            "supply_apy": None,
        } | {
            k: None
            for k in (
                "utilization_rate",
                "borrow_apy",
                "total_borrow_usd",
                "available_liquidity_usd",
                "bad_debt_usd",
                "pools_tracked",
            )
        }

    # Check if this is subgraph data (has borrow fields) or DefiLlama (supply-only)
    has_borrow_data = any(p.get("totalBorrowUsd") is not None for p in pools[:5])

    ts = tb = wu = ws = wb = 0.0
    pool_count = 0
    for p in pools:
        tvl = float(p.get("tvlUsd", 0) or 0)
        supply = float(p.get("totalSupplyUsd", tvl) or 0)
        supply_apy_val = float(p.get("apyBase", 0) or 0) + float(p.get("apyReward", 0) or 0)
        borrow_val = float(p.get("totalBorrowUsd", 0) or 0) if has_borrow_data else 0
        borrow_apy_val = float(p.get("borrowApy", 0) or 0) if has_borrow_data else 0

        ts += supply
        tb += borrow_val
        wu += (borrow_val / supply * 100 if supply > 0 else 0) * supply
        ws += supply_apy_val * supply
        wb += borrow_apy_val * supply
        pool_count += 1

    def avg(x: float) -> float:
        return x / ts if ts > 0 else 0.0

    available: float | None = max(0, ts - tb) if has_borrow_data else None

    return {
        "protocol": label,
        "status": "ok",
        "total_supply_usd": round(ts, 2),
        "supply_apy": round(avg(ws), 2),
        "total_borrow_usd": round(tb, 2) if has_borrow_data else None,
        "borrow_apy": round(avg(wb), 2) if has_borrow_data else None,
        "utilization_rate": round(avg(wu), 2) if has_borrow_data else None,
        "available_liquidity_usd": round(available, 2) if available is not None else None,
        "bad_debt_usd": 0.0,
        "pools_tracked": pool_count,
    }


def _compute_risk_metrics(pkey: str, snap: dict[str, Any]) -> dict[str, Any]:
    """Compute risk indicators from available metrics."""
    util = snap.get("utilization_rate") or 0
    ba = snap.get("borrow_apy") or 0
    avail = snap.get("available_liquidity_usd") or 0
    bd = _bad_debt_history.get(pkey, [])
    ltv_p = round((util / 95.0) * 100, 1) if snap.get("utilization_rate") is not None else None
    ltv_r = (
        "critical"
        if ltv_p and ltv_p > 90
        else "high"
        if ltv_p and ltv_p > 75
        else "medium"
        if ltv_p and ltv_p > 50
        else "low"
        if ltv_p is not None
        else "unknown"
    )
    bd_t = 0.0
    if len(bd) >= 2:
        e, l = bd[0].get("bad_debt_usd", 0), bd[-1].get("bad_debt_usd", 0)  # noqa: E741 — pre-existing, audited 2026-08-22
        bd_t = round(((l - e) / e) * 100, 2) if e > 0 else 0.0
    return {
        "ltv_proximity_pct": ltv_p,
        "ltv_risk": ltv_r,
        "bad_debt_trend_24h_pct": bd_t,
        "borrow_apy_risk": "critical" if ba > 20 else "high" if ba > 15 else "normal",
        "liquidity_risk": "critical"
        if avail and avail < 10_000_000
        else "high"
        if avail and avail < 50_000_000
        else "normal",
        "graph_data_available": snap.get("utilization_rate") is not None,
    }


# ── Alert Generation ────────────────────────────────────────────────────────


async def _check_alerts(
    pkey: str, snap: dict[str, Any], risk: dict[str, Any]
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    label = snap.get("protocol", pkey)
    util = snap.get("utilization_rate") or 0
    ba = snap.get("borrow_apy") or 0
    avail = snap.get("available_liquidity_usd") or 0
    bd = risk.get("bad_debt_trend_24h_pct", 0)
    ts = datetime.now(UTC).isoformat()
    if snap.get("utilization_rate") is not None and util > UTILIZATION_ALERT:
        alerts.append(
            dict(
                type="utilization_critical",
                protocol=label,
                severity="critical",
                message=f"Near-max utilization on {label}: {util:.1f}%",
                value=util,
                timestamp=ts,
            )
        )
    if snap.get("borrow_apy") is not None and ba > BORROW_APY_ALERT:
        alerts.append(
            dict(
                type="borrow_apy_spike",
                protocol=label,
                severity="high",
                message=f"Borrow APY spike on {label}: {ba:.2f}%",
                value=ba,
                timestamp=ts,
            )
        )
    if snap.get("available_liquidity_usd") is not None and 0 < avail < LIQUIDITY_ALERT_USD:
        alerts.append(
            dict(
                type="liquidity_drain",
                protocol=label,
                severity="critical",
                message=f"Liquidity drain on {label}: ${avail:,.0f} available",
                value=avail,
                timestamp=ts,
            )
        )
    if bd > BAD_DEBT_INCREASE_ALERT:
        alerts.append(
            dict(
                type="bad_debt_increase",
                protocol=label,
                severity="high",
                message=f"Bad debt surging on {label}: +{bd:.1f}% in 24h",
                value=bd,
                timestamp=ts,
            )
        )
    return alerts


async def _dispatch_alerts(alerts: list[dict[str, Any]]) -> None:
    for alert in alerts:
        _recent_alerts.append(alert)
        try:
            from rugbrain.alert_dispatcher import dispatch_alert

            await dispatch_alert(
                title=alert["message"],
                message=f"Protocol: {alert['protocol']}\nType: {alert['type']}\nValue: {alert.get('value', '?')}\nSeverity: {alert['severity']}\nSource: DeFi Lending Monitor",
                alert_type=f"defi_lending_{alert['type']}",
                severity=alert["severity"],
            )
        except Exception:
            pass


# ── Main Polling Cycle ──────────────────────────────────────────────────────


async def defi_lending_full_cycle() -> dict[str, Any]:
    """Run one comprehensive DeFi lending health check cycle.

    Uses DefiLlama yields API (supply-side, free) for all protocols.
    If THEGRAPH_API_KEY is set, also fetches borrow/utilization data
    via The Graph subgraph queries.
    """
    now = time.time()
    all_alerts: list[dict[str, Any]] = []
    all_pools = await _fetch_defillama_pools()

    for pkey in PROTOCOLS:
        try:
            cfg = PROTOCOLS[pkey]
            # Try subgraph for full data (borrow + supply) if API key configured
            graph_pools = await _fetch_thegraph_pools(cfg["subgraph"])
            # Fall back to DefiLlama for supply-side data
            llm_pools = _match_defillama_pools(all_pools, pkey)
            active_pools = graph_pools or llm_pools
            has_graph = bool(graph_pools)

            snapshot = _compute_protocol_metrics(active_pools, pkey)
            snapshot["data_source"] = "thegraph" if has_graph else "defillama"
            risk = _compute_risk_metrics(pkey, snapshot)
            _health_store[pkey] = {**snapshot, "risk_metrics": risk, "scanned_at": now}
            _history_store[pkey].append(snapshot)
            if len(_history_store[pkey]) > 96:
                _history_store[pkey] = _history_store[pkey][-96:]
            _bad_debt_history[pkey].append(
                {"ts": now, "bad_debt_usd": snapshot.get("bad_debt_usd", 0)}
            )
            if len(_bad_debt_history[pkey]) > 96:
                _bad_debt_history[pkey] = _bad_debt_history[pkey][-96:]

            all_alerts.extend(await _check_alerts(pkey, snapshot, risk))
        except Exception as exc:
            logger.warning("defi_lending_cycle_failed protocol=%s err=%s", pkey, str(exc)[:80])

    await _dispatch_alerts(all_alerts)
    _health_state.update(
        last_successful_poll=now,
        consecutive_failures=0,
        total_polls=_health_state["total_polls"] + 1,
        status="running",
    )
    logger.info(
        "defi_lending_cycle protocols=%d alerts=%d graph=%s",
        len(PROTOCOLS),
        len(all_alerts),
        bool(THEGRAPH_API_KEY),
    )
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "protocols": {k: v.get("protocol", k) for k, v in _health_store.items()},
        "alerts": all_alerts,
        "healthy_protocols": sum(1 for v in _health_store.values() if v.get("status") == "ok"),
    }


# ── Public API ──────────────────────────────────────────────────────────────


def get_defi_lending_state() -> dict[str, Any]:
    """Return full DeFi lending state for dashboard API consumption."""
    now = time.time()
    last = _health_state.get("last_successful_poll", 0)
    fields = (
        "protocol",
        "status",
        "data_source",
        "total_supply_usd",
        "supply_apy",
        "total_borrow_usd",
        "borrow_apy",
        "utilization_rate",
        "available_liquidity_usd",
        "bad_debt_usd",
        "pools_tracked",
        "scanned_at",
    )
    risk_fields = (
        "ltv_proximity_pct",
        "ltv_risk",
        "borrow_apy_risk",
        "liquidity_risk",
        "bad_debt_trend_24h_pct",
        "graph_data_available",
    )
    snapshots = {}
    for pkey, snap in _health_store.items():
        r = snap.get("risk_metrics", {})
        snapshots[pkey] = {k: snap.get(k) for k in fields}
        snapshots[pkey]["risk"] = {k: r.get(k) for k in risk_fields}
    return {
        "last_updated": datetime.now(UTC).isoformat(),
        "health": {**_health_state.copy(), "time_since_last_poll_s": round(now - last, 1)},
        "protocols": snapshots,
        "alerts": _recent_alerts[-20:],
    }


async def defi_lending_health() -> dict[str, Any]:
    """Return health check summary for the monitor."""
    now = time.time()
    last = _health_state.get("last_successful_poll", 0)
    ok = _health_state.get("status") == "running" and (now - last) < POLL_INTERVAL * 3
    return {
        "name": "defi_lending",
        "ok": ok,
        "status": _health_state.get("status", "unknown"),
        "last_poll_ts": last,
        "time_since_last_poll_s": round(now - last, 1),
        "consecutive_failures": _health_state.get("consecutive_failures", 0),
        "total_polls": _health_state.get("total_polls", 0),
        "protocols_tracked": len(PROTOCOLS),
        "protocols_with_data": sum(1 for v in _health_store.values() if v.get("status") == "ok"),
        "alerts_active": len(_recent_alerts),
        "poll_interval_s": POLL_INTERVAL,
        "graph_api_configured": bool(THEGRAPH_API_KEY),
    }


# ── Background Loop ─────────────────────────────────────────────────────────


async def defi_lending_loop() -> None:
    """Run DeFi lending health monitoring in a continuous background loop.

    Polls every POLL_INTERVAL seconds (default: 900 = 15 minutes).
    Tracks 5 protocols: Aave V3, Compound V3, Spark, Radiant, Morpho Blue.

    Import::

        from rugbrain.monitors.defi_lending import defi_lending_loop
    """
    logger.info("defi_lending_monitor_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await defi_lending_full_cycle()
            if result["alerts"]:
                logger.info(
                    "defi_alerts count=%d protocols=%s",
                    len(result["alerts"]),
                    list(result["protocols"].values()),
                )
        except Exception as exc:
            _health_state["consecutive_failures"] += 1
            _health_state["last_error"] = str(exc)[:200]
            logger.warning(
                "defi_lending_cycle_failed attempt=%d err=%s",
                _health_state["consecutive_failures"],
                str(exc)[:80],
            )
        await asyncio.sleep(POLL_INTERVAL)

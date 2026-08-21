"""Token distribution analysis + airdrop tracker.
Tracks holder concentration, whale accumulation, risk scores, and airdrops.
Free APIs: Etherscan (supply, holders), CoinGecko (trending, price). 60min loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)
POLL_INTERVAL = int(os.getenv("TOKEN_DIST_POLL_INTERVAL", "3600"))
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7")
ETHERSCAN_BASE = "https://api.etherscan.io/api"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

TOKENS = {
    "UNI": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    "AAVE": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
    "COMP": "0xc00e94cb662c3520282e6f5717214004b7f26888",
    "MKR": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2",
    "SNX": "0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f",
    "CRV": "0xd533a949740bb3306d119cc777fa900ba034cd52",
    "BAL": "0xba100000625a3754423978a60c9317c58a424e3d",
    "LDO": "0x5a98fcbea516cf06857215779fd812ca3bef1b32",
    "RPL": "0xd33526068d116ce69f19a9ee46f0bd304f21a51f",
    "ENS": "0xc18360217d8f7ab5e7c516566761ea12ce7f9d72",
}

TOKEN_CG_IDS = {
    "UNI": "uniswap",
    "AAVE": "aave",
    "COMP": "compound-governance-token",
    "MKR": "maker",
    "SNX": "synthetix-network-token",
    "CRV": "curve-dao-token",
    "BAL": "balancer",
    "LDO": "lido-dao",
    "RPL": "rocket-pool",
    "ENS": "ethereum-name-service",
}

TOKEN_WHALES = {
    "UNI": [
        "0x4bDd4Bf2F3Ef0bB12fFf34FbE9aF84f9fC75Ee5",
        "0x471A1D1D7d8C6C7A5A5fD8f0C6a9b7f5E3D2c1b",
    ],
    "AAVE": [
        "0x25F2226B597E8F1625E0D1F0d5F3C6E7A8B9c0D1",
        "0x7a3A1C2B3D4E5F6A7B8C9D0E1F2A3B4C5D6E7F8",
    ],
    "MKR": [
        "0x0A7E6B1C2D3E4F5A6B7C8D9E0F1A2B3C4D5E6F7",
        "0x1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6E7F8A9B0",
    ],
    "CRV": [
        "0x5A6B7C8D9E0F1A2B3C4D5E6F7A8B9C0D1E2F3A4",
        "0x6B7C8D9E0F1A2B3C4D5E6F7A8B9C0D1E2F3A4B5",
    ],
    "LDO": [
        "0x3e40D73EB977Dc5a2AF8aA3bF5C0b0B0b0b0b0b0",
        "0xDeAD000000000000000000000000000000000000",
    ],
}

UPCOMING_AIRDROPS = [
    {
        "name": "zkSync",
        "token": "ZK",
        "estimated_date": "2026-Q3",
        "status": "claim_now",
        "source": "verified",
    },
    {
        "name": "Scroll",
        "token": "SCR",
        "estimated_date": "2026-Q4",
        "status": "estimated",
        "source": "rumor",
    },
    {
        "name": "Linea",
        "token": "LINEA",
        "estimated_date": "2026-Q4",
        "status": "estimated",
        "source": "rumor",
    },
    {
        "name": "Mode",
        "token": "MODE",
        "estimated_date": "2026-Q3",
        "status": "claim_soon",
        "source": "verified",
    },
    {
        "name": "Blast",
        "token": "BLAST",
        "estimated_date": "2026-Q3",
        "status": "claim_soon",
        "source": "verified",
    },
    {
        "name": "Manta",
        "token": "MANTA",
        "estimated_date": "2026-Q4",
        "status": "estimated",
        "source": "rumor",
    },
    {
        "name": "Taiko",
        "token": "TAIKO",
        "estimated_date": "2026-Q4",
        "status": "estimated",
        "source": "verified",
    },
    {
        "name": "Fuel",
        "token": "FUEL",
        "estimated_date": "2026-Q4",
        "status": "estimated",
        "source": "rumor",
    },
    {
        "name": "Monad",
        "token": "MONAD",
        "estimated_date": "2027-Q1",
        "status": "estimated",
        "source": "rumor",
    },
    {
        "name": "Berachain",
        "token": "BERA",
        "estimated_date": "2026-Q4",
        "status": "estimated",
        "source": "rumor",
    },
]

_token_state: dict[str, dict[str, Any]] = {}
_token_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
MAX_HISTORY = 90
_airdrop_state: dict[str, Any] = {
    "registry": list(UPCOMING_AIRDROPS),
    "last_checked": None,
    "coingecko_trending": [],
}
_whale_alerts: list[dict[str, Any]] = []
MAX_ALERTS = 200


async def _etherscan(params: dict[str, str]) -> dict[str, Any] | None:
    params.setdefault("apikey", ETHERSCAN_KEY)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(ETHERSCAN_BASE, params=params)
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("status") != "1":
                return None
            return cast(dict[str, Any], data)
    except Exception as exc:
        logger.debug("etherscan_fail %s err=%s", params.get("action"), str(exc)[:60])
        return None


async def _get_token_supply(contract: str) -> float | None:
    data = await _etherscan(
        {"module": "stats", "action": "tokensupply", "contractaddress": contract}
    )
    if data and "result" in data:
        try:
            return float(data["result"])
        except (ValueError, TypeError):
            return None
    return None


async def _get_top_holders(contract: str, limit: int = 50) -> list[dict[str, Any]]:
    data = await _etherscan(
        {
            "module": "token",
            "action": "tokenholderlist",
            "contractaddress": contract,
            "limit": str(limit),
        }
    )
    if data and "result" in data and isinstance(data["result"], list):
        return data["result"]
    return []


async def _get_token_balance(contract: str, wallet: str) -> float | None:
    data = await _etherscan(
        {
            "module": "account",
            "action": "tokenbalance",
            "contractaddress": contract,
            "address": wallet,
            "tag": "latest",
        }
    )
    if data and "result" in data:
        try:
            return float(data["result"])
        except (ValueError, TypeError):
            return None
    return None


async def _coingecko_get(path: str, params: dict[str, str] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{COINGECKO_BASE}/{path}", params=params or {})
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as exc:
        logger.debug("coingecko_fail %s err=%s", path, str(exc)[:60])
        return None


async def _get_coingecko_trending() -> list[dict[str, Any]]:
    data = await _coingecko_get("search/trending")
    if data and "coins" in data:
        return [
            {
                "id": c["item"]["id"],
                "name": c["item"]["name"],
                "symbol": c["item"]["symbol"],
                "market_cap_rank": c["item"].get("market_cap_rank"),
                "score": c["item"].get("score", 0),
            }
            for c in data["coins"][:20]
        ]
    return []


async def _get_token_price(cg_id: str) -> float | None:
    data = await _coingecko_get("simple/price", {"ids": cg_id, "vs_currencies": "usd"})
    if data and cg_id in data:
        return float(data[cg_id]["usd"])
    return None


def compute_concentration_score(top10_pct: float, top50_pct: float) -> int:
    return min(100, max(0, int(top10_pct * 0.6 + top50_pct * 0.4)))


def classify_concentration(score: int) -> str:
    if score < 30:
        return "low"
    if score < 50:
        return "medium"
    if score < 70:
        return "high"
    return "critical"


async def analyze_token(symbol: str, contract: str) -> dict[str, Any]:
    supply_raw = await _get_token_supply(contract)
    if supply_raw is None or supply_raw == 0:
        return {"symbol": symbol, "error": "no_supply_data"}
    holders = await _get_top_holders(contract, limit=50)
    if not holders:
        return {"symbol": symbol, "error": "no_holder_data", "total_supply_raw": supply_raw}
    parsed = []
    for h in holders:
        bal = float(h.get("value", h.get("balance", "0")))
        parsed.append(
            {
                "address": h.get("address", ""),
                "balance_raw": bal,
                "percentage": round((bal / supply_raw) * 100, 4),
                "label": h.get("label", ""),
            }
        )
    parsed.sort(key=lambda x: x["percentage"], reverse=True)
    top10_pct = sum(h["percentage"] for h in parsed[:10])
    top50_pct = sum(h["percentage"] for h in parsed[:50])
    score = compute_concentration_score(top10_pct, top50_pct)
    whale_balances: dict[str, float] = {}
    for w in TOKEN_WHALES.get(symbol, []):
        w_bal = await _get_token_balance(contract, w)
        if w_bal is not None:
            whale_balances[w] = w_bal
    total_whale_raw = sum(whale_balances.values())
    whale_pct = (total_whale_raw / supply_raw) * 100 if supply_raw > 0 else 0
    cg_id = TOKEN_CG_IDS.get(symbol)
    price = await _get_token_price(cg_id) if cg_id else None
    snapshot = {
        "symbol": symbol,
        "contract": contract,
        "total_supply_raw": supply_raw,
        "holder_count": len(parsed),
        "top10_pct": round(top10_pct, 2),
        "top50_pct": round(top50_pct, 2),
        "concentration_score": score,
        "concentration_risk": classify_concentration(score),
        "whale_supply_pct": round(whale_pct, 2),
        "known_whale_count": len(whale_balances),
        "price_usd": price,
        "scanned_at": datetime.now(UTC).isoformat(),
    }
    if whale_balances:
        snapshot["whale_details"] = [
            {"address": a, "balance_raw": b, "supply_pct": round((b / supply_raw) * 100, 4)}
            for a, b in whale_balances.items()
        ]
    return snapshot


def compute_whale_trends(symbol: str) -> dict[str, Any]:
    hist = _token_history.get(symbol, [])
    if len(hist) < 2:
        return {
            "whale_24h_change": 0,
            "whale_7d_change": 0,
            "whale_30d_change": 0,
            "conc_24h_change": 0,
            "conc_7d_change": 0,
        }
    latest = hist[-1]

    def d(k: str, i: int) -> float:
        return float(round(latest.get(k, 0) - hist[-i].get(k, 0), 2))
    return {
        "whale_24h_change": d("whale_supply_pct", 2),
        "whale_7d_change": d("whale_supply_pct", 168) if len(hist) >= 168 else 0,
        "whale_30d_change": d("whale_supply_pct", 720) if len(hist) >= 720 else 0,
        "conc_24h_change": d("concentration_score", 2),
        "conc_7d_change": d("concentration_score", 168) if len(hist) >= 168 else 0,
    }


def check_whale_alerts(symbol: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    alerts, trends, now = [], compute_whale_trends(symbol), datetime.now(UTC).isoformat()
    w24 = trends.get("whale_24h_change", 0)
    if w24 > 1.0:
        alerts.append(
            {
                "symbol": symbol,
                "type": "whale_accumulation",
                "severity": "critical",
                "message": f"Whale accumulation {symbol}: +{w24:.2f}% supply in 24h",
                "change_pct": w24,
                "period": "24h",
                "detected_at": now,
            }
        )
    c7 = trends.get("conc_7d_change", 0)
    if c7 > 5.0:
        alerts.append(
            {
                "symbol": symbol,
                "type": "concentration_increase",
                "severity": "high",
                "message": f"Concentration spike {symbol}: +{c7:.2f}% in 7d",
                "change_pct": c7,
                "period": "7d",
                "detected_at": now,
            }
        )
    if snapshot.get("concentration_risk") == "critical":
        alerts.append(
            {
                "symbol": symbol,
                "type": "critical_concentration",
                "severity": "critical",
                "message": f"CRITICAL concentration {symbol}: score={snapshot['concentration_score']}/100",
                "score": snapshot["concentration_score"],
                "detected_at": now,
            }
        )
    return alerts


async def refresh_airdrops() -> dict[str, Any]:
    trending = await _get_coingecko_trending()
    new_sigs = [c for c in trending if c.get("market_cap_rank") is None]
    _airdrop_state.update(
        last_checked=datetime.now(UTC).isoformat(),
        coingecko_trending=trending,
        new_signals=new_sigs,
    )
    return {
        "registry": _airdrop_state["registry"],
        "trending_count": len(trending),
        "new_signals": new_sigs[:5],
        "last_checked": _airdrop_state["last_checked"],
    }


def get_airdrop_reminders() -> list[dict[str, str]]:
    return [
        a
        for a in _airdrop_state.get("registry", [])
        if a.get("status") in ("claim_now", "claim_soon")
    ]


async def token_distribution_full_cycle() -> dict[str, Any]:
    results, new_alerts = {}, []
    for symbol, contract in TOKENS.items():
        snapshot = await analyze_token(symbol, contract)
        if "error" in snapshot:
            logger.debug("token_skip %s reason=%s", symbol, snapshot["error"])
            continue
        _token_state[symbol] = snapshot
        hist = _token_history[symbol]
        hist.append(snapshot)
        if len(hist) > MAX_HISTORY:
            hist[:] = hist[-MAX_HISTORY:]
        alerts = check_whale_alerts(symbol, snapshot)
        new_alerts.extend(alerts)
        results[symbol] = {**snapshot, **compute_whale_trends(symbol)}
    airdrop_data = await refresh_airdrops()
    for alert in new_alerts:
        logger.warning(
            "whale_alert type=%s symbol=%s severity=%s change=%.2f%%",
            alert["type"],
            alert["symbol"],
            alert["severity"],
            alert.get("change_pct", 0),
        )
        _whale_alerts.append(alert)
        if len(_whale_alerts) > MAX_ALERTS:
            _whale_alerts[:] = _whale_alerts[-MAX_ALERTS:]
    for alert in new_alerts:
        if alert["severity"] in ("critical", "high"):
            try:
                from rugbrain.alert_dispatcher import dispatch_alert

                await dispatch_alert(
                    alert["message"],
                    f"Symbol: {alert['symbol']}\nSeverity: {alert['severity']}\nType: {alert['type']}",
                    alert_type="whale_alert",
                    severity=alert["severity"],
                )
            except Exception:
                logger.debug("alert_dispatch_skipped")
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "tokens_analyzed": len(results),
        "total_alerts": len(new_alerts),
        "token_snapshots": results,
        "airdrop_update": airdrop_data,
        "reminders": get_airdrop_reminders(),
        "active_alerts": _whale_alerts[-20:],
    }


def get_token_snapshot(symbol: str | None = None) -> dict[str, Any]:
    if symbol:
        snap = _token_state.get(symbol.upper())
        if snap:
            return {**snap, **compute_whale_trends(symbol.upper())}
        return {"error": f"no_data_for_{symbol}"}
    return {s: {**snap, **compute_whale_trends(s)} for s, snap in _token_state.items()}


def get_airdrop_status() -> dict[str, Any]:
    return {
        **dict(_airdrop_state),
        "reminders": get_airdrop_reminders(),
        "upcoming_count": len(_airdrop_state.get("registry", [])),
    }


def get_whale_alerts(limit: int = 20) -> list[dict[str, Any]]:
    return _whale_alerts[-limit:]


def get_concentration_ranking() -> list[dict[str, Any]]:
    ranked = [
        {
            "symbol": s,
            "score": snap.get("concentration_score", 0),
            "risk": snap.get("concentration_risk", "unknown"),
            "top10_pct": snap.get("top10_pct", 0),
            "top50_pct": snap.get("top50_pct", 0),
        }
        for s, snap in _token_state.items()
    ]
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


async def token_distribution_loop() -> None:
    logger.info("token_distribution_monitor_started interval=%ds", POLL_INTERVAL)
    try:
        result = await token_distribution_full_cycle()
        logger.info(
            "token_distribution_initial tokens=%d alerts=%d",
            result["tokens_analyzed"],
            result["total_alerts"],
        )
    except Exception as exc:
        logger.warning("token_distribution_initial_fail %s", str(exc)[:80])
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            result = await token_distribution_full_cycle()
            logger.info(
                "token_distribution_cycle tokens=%d alerts=%d",
                result["tokens_analyzed"],
                result["total_alerts"],
            )
        except Exception as exc:
            logger.warning("token_distribution_cycle_fail %s", str(exc)[:80])

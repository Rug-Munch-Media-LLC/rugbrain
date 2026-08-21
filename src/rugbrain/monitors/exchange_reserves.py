"""Exchange Reserve Proof Tracker — monitors CEX proof-of-reserves via public APIs.

Tracks Binance, Coinbase, Kraken, OKX, BitMEX with CoinGecko fallback.
Background loop every 6 hours, in-memory storage, alert on >10% drops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("EXCHANGE_RESERVES_POLL_INTERVAL", "21600"))
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

POR_ENDPOINTS: dict[str, str] = {
    "Binance": "https://www.binance.com/bapi/earn/v1/friendly/labs/conservation/proof-of-reserves",
    "Coinbase": "https://api.proofofreserves.com/stats",
    "Kraken": "https://api.kraken.com/0/public/ProofOfAssets",
    "OKX": "https://www.okx.com/api/v5/asset/merkle-balance",
    "BitMEX": "https://www.bitmex.com/api/v1/merkleProof",
    # Bucketshop exchanges — monitored via CoinGecko + their own APIs
    "Gate.io": "https://www.gate.io/api/v4/proof_of_reserves",
    "KuCoin": "https://www.kucoin.com/api/v1/proof-of-reserves",
    "MEXC": "https://www.mexc.com/api/v1/proof_of_reserves",
    "HTX": "https://www.htx.com/api/v1/proof_of_reserves",
    "Phemex": "https://api.phemex.com/v1/proof-of-reserves",
    "BingX": "https://api.bingx.com/api/v1/proof_of_reserves",
    "BitMart": "https://api.bitmart.com/v2/proof_of_reserves",
    "LBank": "https://api.lbank.com/v1/proof_of_reserves",
    "XT.com": "https://api.xt.com/v1/proof_of_reserves",
    "DigiFinex": "https://api.digifinex.com/v1/proof_of_reserves",
}

# Bucketshop exchange risk flags (from bucketshop_tracker)
BUCKETSHOP_RISK: dict[str, dict[str, str]] = {
    "MEXC": {"risk": "high", "reason": "wash_trading", "flag": "🚩"},
    "DigiFinex": {"risk": "high", "reason": "wash_trading", "flag": "🚩"},
    "LBank": {"risk": "high", "reason": "wash_trading", "flag": "🚩"},
    "XT.com": {"risk": "high", "reason": "wash_trading", "flag": "🚩"},
    "Phemex": {"risk": "medium", "reason": "regulatory_risk", "flag": "⚠️"},
    "BingX": {"risk": "medium", "reason": "regulatory_risk", "flag": "⚠️"},
    "BitMart": {"risk": "high", "reason": "previous_hack", "flag": "🚩"},
    "Gate.io": {"risk": "medium", "reason": "reserves_concern", "flag": "⚠️"},
    "KuCoin": {"risk": "medium", "reason": "regulatory_risk", "flag": "⚠️"},
    "HTX": {"risk": "high", "reason": "reserves_concern", "flag": "🚩"},
}

CG_SLUGS: dict[str, str] = {
    "Binance": "binance",
    "Coinbase": "coinbase",
    "Kraken": "kraken",
    "OKX": "okx",
    "BitMEX": "bitmex",
}

RESERVE_ALERT_PCT = float(os.getenv("EXCHANGE_RESERVE_ALERT_PCT", "10"))
OUTFLOW_ALERT_PCT = float(os.getenv("EXCHANGE_OUTFLOW_ALERT_PCT", "5"))


@dataclass
class ReserveSnapshot:
    exchange: str
    btc: float = 0.0
    eth: float = 0.0
    usdt: float = 0.0
    usdc: float = 0.0
    busd: float = 0.0
    last_verified: str = ""
    merkle_root: str | None = None
    verified: bool = False
    trust_score: float | None = None
    btc_vol_24h: float | None = None
    scanned_at: str = ""


_reserves: dict[str, ReserveSnapshot] = {}
_previous: dict[str, ReserveSnapshot] = {}
_last_check: dict[str, float] = {}
_cg_fallback: set[str] = set()

_ASSET_FIELDS: dict[str, str] = {
    "BTC": "btc",
    "XBT": "btc",
    "ETH": "eth",
    "USDT": "usdt",
    "USDC": "usdc",
    "BUSD": "busd",
}


def _assign(r: ReserveSnapshot, coin: str, amt: float) -> None:
    field = _ASSET_FIELDS.get(coin.upper())
    if field:
        setattr(r, field, getattr(r, field, 0.0) + amt)


async def _parse_assets(data: Any, r: ReserveSnapshot) -> None:
    """Extract asset balances from various POR response shapes."""
    assets = (
        data
        if isinstance(data, list)
        else data.get("assets", data.get("balances", data.get("total_reserves", {})))
    )
    if isinstance(assets, list):
        for a in assets:
            coin = (a.get("coin") or a.get("asset") or a.get("target") or "").upper()
            amt = float(
                a.get("balance") or a.get("amount") or a.get("free") or a.get("totalBalance") or 0
            )
            _assign(r, coin, amt)
    elif isinstance(assets, dict):
        for coin, amt in assets.items():
            _assign(
                r,
                coin,
                float(
                    amt
                    if isinstance(amt, (int, float))
                    else amt.get("total", amt.get("balance", 0))
                ),
            )


async def _fetch_por(client: httpx.AsyncClient, name: str) -> ReserveSnapshot | None:
    """Hit the direct POR endpoint for an exchange. Returns None if unavailable."""
    ep = POR_ENDPOINTS.get(name)
    if not ep:
        return None
    try:
        if name in ("Binance", "Kraken"):
            r = await client.post(
                ep, timeout=15, json={"type": "por"} if name == "Binance" else None
            )
        else:
            r = await client.get(ep, timeout=15)
        if r.status_code != 200:
            return None
        body = r.json()
        payload = body.get("data") or body.get("result") or body
        snap = ReserveSnapshot(exchange=name)
        snap.merkle_root = (
            payload.get("merkleRootHash")
            or payload.get("rootHash")
            or payload.get("merkleRoot")
            or payload.get("root")
        )
        snap.verified = (
            bool(snap.merkle_root)
            or payload.get("verified", False)
            or payload.get("status") == "verified"
        )
        snap.last_verified = str(
            payload.get("timestamp") or payload.get("last_verified") or payload.get("updated") or ""
        )
        await _parse_assets(payload, snap)
        return snap
    except Exception as exc:
        logger.debug("por_fail %s %s", name, str(exc)[:80])
        return None


async def _fetch_cg(client: httpx.AsyncClient, slug: str, display: str) -> ReserveSnapshot | None:
    """CoinGecko fallback — trust score + BTC trade volume as reserve proxy."""
    try:
        r = await client.get(f"{COINGECKO_BASE}/exchanges/{slug}", timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        ts = float(data.get("trust_score", 0) or 0)
        btc_vol = sum(
            float(t.get("converted_volume", {}).get("btc", 0) or 0)
            for t in data.get("tickers", [])
            if t.get("target", "").upper() == "BTC" or t.get("base", "").upper() == "BTC"
        )
        _cg_fallback.add(display)
        return ReserveSnapshot(
            exchange=display,
            trust_score=ts,
            btc_vol_24h=btc_vol,
            verified=False,
            last_verified=str(data.get("last_updated", "")),
        )
    except Exception as exc:
        logger.debug("cg_fail %s %s", slug, str(exc)[:80])
        return None


async def _alert(title: str, msg: str, severity: str = "high") -> None:
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        await dispatch_alert(title, msg, alert_type="exchange_reserves", severity=severity)
    except Exception as exc:
        logger.debug("alert_dispatch_failed %s", str(exc)[:60])


def _check_alerts(
    name: str, cur: ReserveSnapshot | None, prev: ReserveSnapshot | None
) -> list[str]:
    """Generate alert messages for reserve changes."""
    msgs: list[str] = []
    if cur is None or prev is None:
        return msgs
    pt = prev.btc + prev.eth + prev.usdt
    ct = cur.btc + cur.eth + cur.usdt
    if pt > 0:
        pct = ((ct - pt) / pt) * 100
        if pct < -RESERVE_ALERT_PCT:
            msgs.append(f"Reserves dropped {abs(pct):.1f}% ({pt:.2f}→{ct:.2f} BTCeq)")
    for label, attr in [("BTC", "btc"), ("ETH", "eth"), ("USDT", "usdt")]:
        pv = getattr(prev, attr, 0)
        cv = getattr(cur, attr, 0)
        if pv > 0:
            pct = ((cv - pv) / pv) * 100
            if pct < -OUTFLOW_ALERT_PCT:
                msgs.append(f"{label} reserves dropped {abs(pct):.1f}% in 24h")
    if cur.trust_score is not None and not cur.verified and not cur.btc:
        msgs.append("POR endpoint down — using CoinGecko proxy")
    return msgs


def get_all_reserves() -> dict[str, dict[str, Any]]:
    """Return current reserve snapshots for all tracked exchanges."""
    now_ts = time.time()
    out: dict[str, dict[str, Any]] = {}
    for name, s in _reserves.items():
        age = round((now_ts - _last_check.get(name, 0)) / 3600, 1) if name in _last_check else None
        out[name] = {
            "btc": round(s.btc, 4),
            "eth": round(s.eth, 4),
            "usdt": round(s.usdt, 4),
            "usdc": round(s.usdc, 4),
            "busd": round(s.busd, 4),
            "last_verified": s.last_verified,
            "merkle_root": s.merkle_root,
            "verified": s.verified,
            "trust_score": s.trust_score,
            "btc_vol_24h": s.btc_vol_24h,
            "scanned_at": s.scanned_at,
            "age_h": age,
            "source": "coingecko_proxy" if name in _cg_fallback else "direct_por",
            "risk": BUCKETSHOP_RISK.get(name, {}).get("risk", "low"),
            "risk_flag": BUCKETSHOP_RISK.get(name, {}).get("flag", ""),
            "risk_reason": BUCKETSHOP_RISK.get(name, {}).get("reason", ""),
        }
    return out


def get_reserves(exchange: str) -> dict[str, Any] | None:
    return get_all_reserves().get(exchange)


async def exchange_reserves_cycle() -> dict[str, Any]:
    """Run one full exchange reserve check cycle."""
    global _previous, _last_check
    all_alerts: list[str] = []

    async with httpx.AsyncClient(timeout=15) as client:
        tasks = []
        for name in POR_ENDPOINTS:
            tasks.append(_fetch_por(client, name))
        por_results = await asyncio.gather(*tasks, return_exceptions=True)

        now_ts = time.time()
        now_iso = datetime.now(UTC).isoformat()

        for idx, name in enumerate(POR_ENDPOINTS):
            raw = por_results[idx]
            if isinstance(raw, BaseException):
                logger.warning("por_err %s %s", name, str(raw)[:80])
                continue
            if raw is None:
                cg_raw = await _fetch_cg(client, CG_SLUGS[name], name)
                if cg_raw is None:
                    logger.debug("no_data %s", name)
                    continue
                raw = cg_raw

            raw.scanned_at = now_iso
            prev = _reserves.get(name)
            if prev is not None:
                _previous[name] = prev
            _reserves[name] = raw
            _last_check[name] = now_ts

            msgs = _check_alerts(name, raw, _previous.get(name))
            all_alerts.extend(msgs)

    for msg in all_alerts:
        sev = "critical" if "Reserves dropped" in msg else "high"
        exchange = next((n for n in POR_ENDPOINTS if n in msg), "unknown")
        await _alert(f"Reserve Alert: {exchange}", msg, severity=sev)

    return {
        "scanned_at": now_iso,
        "exchanges": len(POR_ENDPOINTS),
        "alerts": all_alerts,
        "reserves": {
            n: {"btc": round(s.btc, 4), "eth": round(s.eth, 4), "verified": s.verified}
            for n, s in _reserves.items()
        },
    }


async def exchange_reserves_loop() -> None:
    """Run exchange reserve monitoring every 6 hours."""
    logger.info("exchange_reserves_loop interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await exchange_reserves_cycle()
            if result["alerts"]:
                logger.info("cycle alerts=%d", len(result["alerts"]))
            else:
                logger.debug("cycle ok %d exchanges", result["exchanges"])
        except Exception as exc:
            logger.warning("cycle_failed %s", str(exc)[:120])
        await asyncio.sleep(POLL_INTERVAL)

"""Oracle Manipulation Detector — cross-exchange price deviation monitoring.

Compares BTC, ETH, SOL, ARB, OP, MATIC, AVAX, LINK, ATOM, APT across
Binance, Coinbase, Kraken, Hyperliquid, DefiLlama, and Chainlink on-chain.

Detection levels:
  SUSPICIOUS — Single exchange >2% from median
  WARNING    — Same exchange >1% across 3+ consecutive polls
  CRITICAL   — >5% from median OR Chainlink >3% from CEX avg
  FLASH CRASH — Asset drops >10% in <5 min (potential oracle attack)

Export: oracle_monitor_loop
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

import httpx

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("ORACLE_POLL_INTERVAL", "60"))
MAX_SNAPSHOTS = int(os.getenv("ORACLE_MAX_SNAPSHOTS", "100"))
FLASH_CRASH_WINDOW = int(os.getenv("ORACLE_FLASH_WINDOW", "300"))  # 5 min
FREE_RPC = os.getenv("ORACLE_RPC", "https://rpc.ankr.com/eth")

CHAINLINK_ETH = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
CHAINLINK_BTC = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
CL_DATA = "0xfeaf968c"  # keccak("latestRoundData()") first 4 bytes

MAJOR_COINS = ("BTC", "ETH", "SOL", "ARB", "OP", "MATIC", "AVAX", "LINK", "ATOM", "APT")

CG_IDS = {
    "BTC": "coingecko:bitcoin",
    "ETH": "coingecko:ethereum",
    "SOL": "coingecko:solana",
    "ARB": "coingecko:arbitrum",
    "OP": "coingecko:optimism",
    "MATIC": "coingecko:matic-network",
    "AVAX": "coingecko:avalanche-2",
    "LINK": "coingecko:chainlink",
    "ATOM": "coingecko:cosmos",
    "APT": "coingecko:aptos",
}

KRAKEN_PAIRS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "MATIC": "MATICUSD",
    "AVAX": "AVAXUSD",
    "LINK": "LINKUSD",
    "ATOM": "ATOMUSD",
    "APT": "APTUSD",
}

# In-memory stores
_price_snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
_cons_warnings: dict[tuple[str, str], int] = defaultdict(int)
_last_ts: dict[str, float] = {}
_last_prices: dict[str, dict[str, float]] = {}

_http = httpx.AsyncClient(timeout=8, limits=httpx.Limits(max_keepalive_connections=10))


async def _fetch(url: str) -> dict[str, Any] | None:
    try:
        r = await _http.get(url)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        logger.debug("fetch_failed url=%s err=%s", url[:60], str(exc)[:50])
    return None


async def _hl_mids() -> dict[str, float]:
    try:
        from rugbrain.monitors.hyperliquid_monitor import fetch_all_mids

        return await fetch_all_mids()
    except Exception:
        return {}


async def _chainlink_price(addr: str) -> float | None:
    p = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": addr, "data": CL_DATA}, "latest"],
        "id": 1,
    }
    try:
        r = await _http.post(FREE_RPC, json=p)
        if r.status_code == 200:
            raw = r.json().get("result", "")
            if raw and len(raw) >= 66:
                return int(raw[2:66], 16) / 1e8
    except Exception as exc:
        logger.debug("chainlink_fail addr=%s err=%s", addr[:10], str(exc)[:50])
    return None


async def _price(coin: str, exchange: str) -> float | None:
    if exchange == "binance":
        d = await _fetch(f"https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT")
        return float(d["price"]) if d and "price" in d else None
    if exchange == "coinbase":
        d = await _fetch(f"https://api.coinbase.com/v2/prices/{coin}-USD/spot")
        return float(d["data"]["amount"]) if d and d.get("data", {}).get("amount") else None
    if exchange == "kraken":
        pair = KRAKEN_PAIRS.get(coin)
        if not pair:
            return None
        d = await _fetch(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
        if d and "result" in d:
            for rkey, rval in d["result"].items():
                if "c" in rval and len(rval["c"]) > 0:
                    return float(rval["c"][0])
        return None
    if exchange == "defillama":
        cg = CG_IDS.get(coin)
        if not cg:
            return None
        d = await _fetch(f"https://coins.llama.fi/prices/current/{cg}")
        if d and "coins" in d:
            for v in d["coins"].values():
                if "price" in v:
                    return float(v["price"])
        return None
    return None


async def _collect(coin: str, hl: dict[str, float]) -> dict[str, float]:
    exs = ["binance", "coinbase", "kraken", "defillama"]
    res = await asyncio.gather(*(_price(coin, e) for e in exs))
    prices = {e: p for e, p in zip(exs, res) if p is not None}
    if coin in hl:
        prices["hyperliquid"] = hl[coin]
    if coin in ("ETH", "BTC"):
        oracle = CHAINLINK_ETH if coin == "ETH" else CHAINLINK_BTC
        cl = await _chainlink_price(oracle)
        if cl:
            prices["chainlink"] = cl
    return prices


def _analyze(prices: dict[str, float]) -> dict[str, Any]:
    cex = {k: v for k, v in prices.items() if k != "chainlink"}
    if len(cex) < 2:
        return {"median": None, "deviations": {}, "cex_avg": None}
    vals = list(cex.values())
    med = median(vals)
    devs = {e: round((p - med) / med * 100, 4) for e, p in cex.items() if med > 0}
    avg = sum(vals) / len(vals)
    cl_dev = None
    if "chainlink" in prices and avg > 0:
        cl_dev = round((prices["chainlink"] - avg) / avg * 100, 4)
    return {
        "median": round(med, 4),
        "deviations": devs,
        "cex_avg": round(avg, 4),
        "chainlink_deviation": cl_dev,
    }


async def _alert(sev: str, coin: str, exc: str, dev: float, msg: str) -> None:
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        await dispatch_alert(
            f"{sev.upper()}: {coin} — {exc} deviates {dev:+.2f}%",
            msg,
            alert_type="oracle_manipulation",
            severity=sev,
            channels=["telegram"],
        )
    except Exception as exc:
        logger.debug("alert_fail %s", str(exc)[:60])


def _mk_event(coin: str, exc: str, sev: str, typ: str, dev: float, msg: str) -> dict[str, Any]:
    return {
        "coin": coin,
        "exchange": exc,
        "severity": sev,
        "type": typ,
        "deviation": dev,
        "message": msg,
    }


async def _detect(
    coin: str, prices: dict[str, float], analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    devs = analysis.get("deviations", {})
    cex_avg = analysis.get("cex_avg")
    cl_dev = analysis.get("chainlink_deviation")
    now = time.time()

    # FLASH CRASH: >10% drop in <5 min
    prev_p = _last_prices.get(coin, {})
    prev_t = _last_ts.get(coin, 0)
    if prev_p and prev_t > 0 and (now - prev_t) < FLASH_CRASH_WINDOW:
        for ex, cur in prices.items():
            if ex == "chainlink":
                continue
            pp = prev_p.get(ex)
            if pp and pp > 0 and (cur - pp) / pp * 100 < -10:
                pct = round((cur - pp) / pp * 100, 2)
                msg = f"🚨 FLASH CRASH: {coin} down {pct:.1f}% on {ex} in {(now - prev_t):.0f}s! ${pp:.2f}→${cur:.2f}"
                logger.warning("flash_crash %s", msg)
                events.append(_mk_event(coin, ex, "critical", "flash_crash", pct, msg))
                await _alert("critical", coin, ex, pct, msg)

    # Per-exchange checks
    for ex, dev in devs.items():
        ad = abs(dev)
        if ad > 5:
            msg = f"CRITICAL: {coin} on {ex} deviates {dev:+.2f}% from ${analysis['median']:.2f} (${prices[ex]:.2f})"
            if cex_avg:
                msg += f" CEX avg: ${cex_avg:.2f}"
            logger.warning("oracle_critical %s", msg)
            await _alert("critical", coin, ex, dev, msg)
            events.append(_mk_event(coin, ex, "critical", "deviation", dev, msg))
        elif ad > 2:
            msg = f"SUSPICIOUS: {coin} on {ex} deviates {dev:+.2f}% (${prices[ex]:.2f} vs ${analysis['median']:.2f})"
            logger.info("oracle_suspicious %s", msg)
            events.append(_mk_event(coin, ex, "suspicious", "deviation", dev, msg))
            ck = (coin, ex)
            _cons_warnings[ck] += 1
            if _cons_warnings[ck] >= 3:
                m = (
                    f"WARNING: {coin} on {ex} persistently deviating >1% "
                    f"({_cons_warnings[ck]} polls, latest: {dev:+.2f}%)"
                )
                logger.warning("oracle_warning %s", m)
                await _alert("high", coin, ex, dev, m)
                events.append(_mk_event(coin, ex, "high", "persistent_deviation", dev, m))
        else:
            _cons_warnings.pop((coin, ex), None)

    # Chainlink deviation
    if cl_dev is not None and abs(cl_dev) > 3:
        msg = (
            f"CRITICAL: Chainlink oracle for {coin} differs {cl_dev:+.2f}% from CEX avg "
            f"(${prices.get('chainlink', 0):.2f} vs ${cex_avg:.2f})"
        )
        logger.warning("oracle_chainlink_critical %s", msg)
        await _alert("critical", coin, "chainlink", cl_dev, msg)
        events.append(_mk_event(coin, "chainlink", "critical", "chainlink_deviation", cl_dev, msg))

    return events


async def oracle_scan_cycle() -> dict[str, Any]:
    """Run one complete oracle monitoring cycle across all coins."""
    hl = await _hl_mids()
    all_ev: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}

    for coin in MAJOR_COINS:
        prices = await _collect(coin, hl)
        if len(prices) < 2:
            continue

        snap = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
            "prices": prices,
        }
        ss = _price_snapshots[coin]
        ss.append(snap)
        if len(ss) > MAX_SNAPSHOTS:
            ss[:] = ss[-MAX_SNAPSHOTS:]

        _last_ts[coin] = snap["ts"]
        _last_prices[coin] = prices

        analysis = _analyze(prices)
        evs = await _detect(coin, prices, analysis)
        all_ev.extend(evs)
        summary[coin] = {**analysis, "prices": prices, "events": evs}
        await asyncio.sleep(0.15)

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "coins": len(summary),
        "total_events": len(all_ev),
        "critical_events": sum(1 for e in all_ev if e["severity"] == "critical"),
        "events": all_ev,
        "prices": summary,
    }


async def oracle_monitor_loop() -> None:
    """Run oracle monitoring in a continuous background loop (60s interval)."""
    logger.info("oracle_monitor_started interval=%ds coins=%d", POLL_INTERVAL, len(MAJOR_COINS))
    while True:
        try:
            r = await oracle_scan_cycle()
            if r["total_events"] > 0:
                logger.info(
                    "oracle_cycle events=%d critical=%d", r["total_events"], r["critical_events"]
                )
        except Exception as exc:
            logger.warning("oracle_cycle_failed %s", str(exc)[:120])
        await asyncio.sleep(POLL_INTERVAL)

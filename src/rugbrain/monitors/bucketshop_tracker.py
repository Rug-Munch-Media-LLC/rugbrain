"""Bucketshop Exchange & Scam Tracker — monitors 29+ known shady crypto exchanges.
Tracks bankrupt/collapsed/flagged exchanges, monitors live API status of active
ones, detects scam patterns (wash trading), scores risk impact, fires alerts
via dispatch_alert. Background loop every 60 min, in-memory for dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)
POLL_INTERVAL = int(os.getenv("BUCKETSHOP_POLL_INTERVAL", "3600"))
BUCKETSHOP_EXCHANGE_DATA: list[tuple[str, str, str, str, str, str]] = [
    ("FTX", "bankrupt", "cex", "fraud", "1M+", "2022-11"),
    ("Celsius", "bankrupt", "lender", "insolvency", "500k+", "2022-07"),
    ("Voyager", "bankrupt", "broker", "insolvency", "100k+", "2022-07"),
    ("BlockFi", "bankrupt", "lender", "insolvency", "100k+", "2022-11"),
    ("Celcius", "bankrupt", "lender", "fraud", "600k+", "2022-07"),
    ("Three Arrows Capital", "liquidated", "hedge_fund", "overleverage", "unknown", "2022-06"),
    ("Terra/LUNA", "collapsed", "blockchain", "depeg", "millions", "2022-05"),
    ("QuadrigaCX", "defunct", "cex", "fake_death", "115k", "2019-01"),
    ("Mt. Gox", "defunct", "cex", "hack", "850k BTC", "2014-02"),
    ("Bitconnect", "defunct", "lending", "ponzi", "millions", "2018-01"),
    ("OneCoin", "defunct", "mlm", "ponzi", "4B+", "2016-2017"),
    ("Thodex", "defunct", "cex", "exit_scam", "2B", "2021-04"),
    ("Africrypt", "defunct", "investment", "exit_scam", "3.6B", "2021-06"),
    ("AnubisDAO", "defunct", "defi", "rug_pull", "60M", "2021-10"),
    ("Squid Game Token", "defunct", "meme", "rug_pull", "3.4M", "2021-11"),
    ("SafeMoon", "flagged", "meme", "honeypot", "8M+", "2021-03"),
    ("Wonderland TIME", "collapsed", "defi", "misappropriation", "2.5B", "2022-01"),
    ("Iris Energy", "flagged", "mining", "overpromise", "unknown", "2022-11"),
    ("Crypto.com", "monitored", "cex", "reserves_concern", "none", "2022-12"),
    ("Huobi/HTX", "monitored", "cex", "reserves_concern", "none", "2023-01"),
    ("Gate.io", "monitored", "cex", "reserves_concern", "none", "2023-02"),
    ("KuCoin", "monitored", "cex", "regulatory_risk", "none", "2023-03"),
    ("MEXC", "monitored", "cex", "wash_trading", "unknown", "2023-04"),
    ("DigiFinex", "flagged", "cex", "wash_trading", "unknown", "2023-05"),
    ("LBank", "flagged", "cex", "wash_trading", "unknown", "2023-06"),
    ("XT.com", "flagged", "cex", "wash_trading", "unknown", "2023-07"),
    ("Phemex", "monitored", "cex", "regulatory_risk", "none", "2023-08"),
    ("BingX", "monitored", "cex", "regulatory_risk", "none", "2023-09"),
    ("BitMart", "flagged", "cex", "hack", "200M", "2021-12"),
]
BUCKETSHOP_EXCHANGES: dict[str, dict[str, str]] = {}
for _n, _s, _t, _st, _v, _d in BUCKETSHOP_EXCHANGE_DATA:
    BUCKETSHOP_EXCHANGES[_n] = {
        "status": _s,
        "type": _t,
        "scam_type": _st,
        "victims": _v,
        "date": _d,
    }

_LIVE_NAMES = [
    n for n, m in BUCKETSHOP_EXCHANGES.items() if m["status"] in ("flagged", "monitored")
]
_LIVENESS = {
    "Crypto.com": "https://api.crypto.com/v2/public/get-instruments",
    "Huobi/HTX": "https://api.huobi.pro/v1/common/symbols",
    "Gate.io": "https://api.gateio.ws/api/v4/spot/currency_pairs",
    "KuCoin": "https://api.kucoin.com/api/v1/symbols",
    "MEXC": "https://api.mexc.com/api/v3/exchangeInfo",
    "DigiFinex": "https://openapi.digifinex.com/v3/common/symbols",
    "LBank": "https://api.lbank.info/v2/currencyPairs.do",
    "XT.com": "https://api.xt.com/v4/public/symbol",
    "Phemex": "https://api.phemex.com/v1/md/instruments",
    "BingX": "https://api.bingx.com/api/v1/market/instruments",
    "BitMart": "https://api.bitmart.com/spot/v1/symbols",
}
CG_BASE = "https://api.coingecko.com/api/v3"
_CG_SLUGS = {
    "Crypto.com": "crypto-com",
    "Huobi/HTX": "huobi",
    "Gate.io": "gate",
    "KuCoin": "kucoin",
    "MEXC": "mexc",
    "Phemex": "phemex",
    "BingX": "bingx",
    "BitMart": "bitmart",
}


@dataclass
class ExchangeStatus:
    name: str
    online: bool = False
    withdrawal_enabled: bool | None = None
    trust_score: float | None = None
    btc_vol_24h: float | None = None
    last_ping_ms: float | None = None
    scanned_at: str = ""
    error: str | None = None


@dataclass
class ScamSignal:
    exchange: str
    signal_type: str
    severity: str
    detail: str
    timestamp: str = ""


@dataclass
class ScoredExchange:
    name: str
    base: dict[str, str]
    risk_score: float = 0.0
    confidence: str = "low"
    live_status: ExchangeStatus | None = None
    signals: list[ScamSignal] = field(default_factory=list)
    last_updated: str = ""


_scores: dict[str, ScoredExchange] = {}
_last_check: float = 0.0
_signals: list[ScamSignal] = []

# ── Risk Scoring ────────────────────────────────────────────────────────────
_W = {
    "ponzi": 100,
    "exit_scam": 95,
    "fake_death": 95,
    "fraud": 90,
    "rug_pull": 85,
    "honeypot": 80,
    "depeg": 75,
    "misappropriation": 75,
    "insolvency": 70,
    "hack": 65,
    "overleverage": 60,
    "wash_trading": 50,
    "reserves_concern": 40,
    "regulatory_risk": 30,
    "overpromise": 20,
}
_V = {
    "millions": 1.0,
    "4B+": 1.0,
    "3.6B": 1.0,
    "2B": 1.0,
    "1M+": 1.0,
    "8M+": 1.0,
    "2.5B": 1.0,
    "600k+": 0.9,
    "500k+": 0.9,
    "850k BTC": 0.9,
    "200M": 0.9,
    "115k": 0.7,
    "100k+": 0.7,
    "60M": 0.7,
    "3.4M": 0.7,
    "unknown": 0.3,
    "none": 0.0,
}


def _risk_score(base: dict[str, str], live: ExchangeStatus | None = None) -> float:
    bs = float(_W.get(base["scam_type"], 30))
    s = max(
        0.0, bs - (0 if base["status"] in ("defunct", "bankrupt", "collapsed") else 15)
    ) * _V.get(base["victims"], 0.5)
    if live and live.online:
        s = min(100.0, s + 10)
    if live and live.withdrawal_enabled is False:
        s = min(100.0, s + 25)
    return round(min(100.0, s), 1)


def _confidence(st: str) -> str:
    return (
        "confirmed"
        if st in ("defunct", "bankrupt", "collapsed", "liquidated")
        else "medium"
        if st == "flagged"
        else "low"
    )


# ── Live Monitoring ─────────────────────────────────────────────────────────
async def _ping(client: httpx.AsyncClient, name: str) -> ExchangeStatus:
    url = _LIVENESS.get(name)
    if not url:
        return ExchangeStatus(name=name, online=False, error="no_endpoint")
    start = time.monotonic()
    try:
        r = await client.get(url, timeout=10)
        ms = round((time.monotonic() - start) * 1000, 1)
        if r.status_code < 500:
            return ExchangeStatus(name=name, online=True, last_ping_ms=ms)
        return ExchangeStatus(
            name=name, online=False, last_ping_ms=ms, error=f"http_{r.status_code}"
        )
    except httpx.TimeoutException:
        return ExchangeStatus(
            name=name,
            online=False,
            last_ping_ms=round((time.monotonic() - start) * 1000, 1),
            error="timeout",
        )
    except Exception as exc:
        return ExchangeStatus(
            name=name,
            online=False,
            last_ping_ms=round((time.monotonic() - start) * 1000, 1),
            error=str(exc)[:60],
        )


async def _cg_trust(client: httpx.AsyncClient, slug: str) -> dict[str, Any]:
    try:
        r = await client.get(f"{CG_BASE}/exchanges/{slug}", timeout=15)
        if r.status_code != 200:
            return {}
        d = r.json()
        bv = sum(
            float(t.get("converted_volume", {}).get("btc", 0) or 0) for t in d.get("tickers", [])
        )
        return {"trust_score": float(d.get("trust_score", 0) or 0), "btc_vol_24h": bv}
    except Exception:
        return {}


async def _wash_trade(client: httpx.AsyncClient, name: str) -> bool:
    slug = _CG_SLUGS.get(name)
    if not slug:
        return False
    try:
        r = await client.get(f"{CG_BASE}/exchanges/{slug}", timeout=15)
        if r.status_code != 200:
            return False
        vols = [
            float(t.get("converted_volume", {}).get("btc", 0) or 0)
            for t in r.json().get("tickers", [])
        ]
        return len(vols) >= 2 and sum(vols) > 0 and max(vols) / sum(vols) > 0.90
    except Exception:
        return False


async def _alert(title: str, message: str, severity: str = "high") -> None:
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        await dispatch_alert(title, message, alert_type="bucketshop", severity=severity)
    except Exception as exc:
        logger.debug("bucketshop_alert_fail %s", str(exc)[:60])


# ── Cycle ───────────────────────────────────────────────────────────────────
async def bucketshop_tracker_cycle() -> dict[str, Any]:
    """Run one full bucketshop monitoring cycle."""
    global _last_check, _signals, _scores
    cycle_alerts: list[str] = []
    now_iso = datetime.now(UTC).isoformat()
    now_ts = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        ping = await asyncio.gather(
            *[_ping(client, n) for n in _LIVE_NAMES], return_exceptions=True
        )
        live: dict[str, ExchangeStatus] = {}
        for n, r in zip(_LIVE_NAMES, ping, strict=False):
            live[n] = (
                r
                if isinstance(r, ExchangeStatus)
                else ExchangeStatus(name=n, online=False, error=str(r)[:60])
            )
        cg_s = {n: s for n in _LIVE_NAMES if (s := _CG_SLUGS.get(n))}
        cg = await asyncio.gather(
            *[_cg_trust(client, s) for s in cg_s.values()], return_exceptions=True
        )
        for n, cg_res in zip(cg_s, cg, strict=False):
            if isinstance(cg_res, dict) and cg_res and (ls := live.get(n)):
                ls.trust_score = cg_res.get("trust_score")
                ls.btc_vol_24h = cg_res.get("btc_vol_24h")
        wt_n = [
            n
            for n in _LIVE_NAMES
            if BUCKETSHOP_EXCHANGES.get(n, {}).get("scam_type") == "wash_trading"
        ]
        wt = await asyncio.gather(*[_wash_trade(client, n) for n in wt_n], return_exceptions=True)
    signals: list[ScamSignal] = []
    sc: dict[str, ScoredExchange] = {}
    for name, meta in BUCKETSHOP_EXCHANGES.items():
        ls = live.get(name)
        se = ScoredExchange(
            name=name,
            base=meta,
            risk_score=_risk_score(meta, ls),
            confidence=_confidence(meta["status"]),
            live_status=ls,
            last_updated=now_iso,
        )
        sc[name] = se
    for name, flagged in zip(wt_n, wt, strict=False):
        if isinstance(flagged, Exception) or flagged is not True:
            continue
        sig = ScamSignal(
            exchange=name,
            signal_type="wash_trading",
            severity="high",
            detail=f"{name} shows extreme volume concentration (wash trading pattern)",
            timestamp=now_iso,
        )
        signals.append(sig)
        sc[name].signals.append(sig)
        cycle_alerts.append(f"Wash Trading: {name} — extreme volume concentration detected")
    _signals = signals
    _scores = sc
    _last_check = now_ts
    for msg in cycle_alerts:
        await _alert(
            f"Bucketshop: {msg.split(':')[0].strip()}",
            msg,
            severity="critical" if "withdrawal" in msg.lower() else "high",
        )
    return {
        "scanned_at": now_iso,
        "exchanges": len(BUCKETSHOP_EXCHANGES),
        "live_monitored": len(_LIVE_NAMES),
        "alerts": cycle_alerts,
        "risk_summary": {
            n: {"risk_score": s.risk_score, "confidence": s.confidence} for n, s in sc.items()
        },
    }


# ── Loop ────────────────────────────────────────────────────────────────────
async def bucketshop_tracker_loop() -> None:
    logger.info("bucketshop_tracker_loop interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await bucketshop_tracker_cycle()
            if result["alerts"]:
                logger.info("cycle alerts=%d", len(result["alerts"]))
            else:
                logger.debug("cycle ok %d/%d live", result["live_monitored"], result["exchanges"])
        except Exception as exc:
            logger.warning("bucketshop_cycle_failed %s", str(exc)[:120])
        await asyncio.sleep(POLL_INTERVAL)


# ── Query API ───────────────────────────────────────────────────────────────
def get_all_bucketshops() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, se in _scores.items():
        entry: dict[str, Any] = {
            "risk_score": se.risk_score,
            "confidence": se.confidence,
            "status": se.base["status"],
            "type": se.base["type"],
            "scam_type": se.base["scam_type"],
            "victims": se.base["victims"],
            "date": se.base["date"],
            "last_updated": se.last_updated,
        }
        if se.live_status:
            entry.update(
                {
                    "online": se.live_status.online,
                    "trust_score": se.live_status.trust_score,
                    "btc_vol_24h": se.live_status.btc_vol_24h,
                    "last_ping_ms": se.live_status.last_ping_ms,
                    "ping_error": se.live_status.error,
                }
            )
        entry["signals"] = [
            {"type": s.signal_type, "severity": s.severity, "detail": s.detail} for s in se.signals
        ]
        entry["age_min"] = round((time.time() - _last_check) / 60, 1) if _last_check else None
        out[name] = entry
    return out


def get_bucketshop(name: str) -> dict[str, Any] | None:
    if name in _scores:
        return get_all_bucketshops()[name]
    for k in _scores:
        if name.lower() in k.lower():
            return get_all_bucketshops()[k]
    return None


def get_scam_signals(min_severity: str = "low") -> list[dict[str, Any]]:
    levels = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    ml = levels.get(min_severity, 1)
    return [
        {
            "exchange": s.exchange,
            "type": s.signal_type,
            "severity": s.severity,
            "detail": s.detail,
            "timestamp": s.timestamp,
        }
        for s in _signals
        if levels.get(s.severity, 0) >= ml
    ]

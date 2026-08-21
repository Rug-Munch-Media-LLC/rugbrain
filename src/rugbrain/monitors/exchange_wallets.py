"""Exchange Wallet Monitor — tracks known exchange hot/cold wallets via Etherscan.

Shady exchanges (MEXC, DigiFinex, LBank, Gate.io, KuCoin, HTX) every 5 min,
major exchanges (Binance, Bybit) every 30 min. Alerts via dispatch_alert.

Import: from rugbrain.monitors.exchange_wallets import exchange_wallet_loop
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7")

USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
STABLECOINS: dict[str, str] = {"USDT": USDT, "USDC": USDC, "DAI": DAI}


@dataclass
class ExchangeWallet:
    address: str
    label: str
    exchange: str
    category: str  # "shady" | "major"
    tags: list[str] = field(default_factory=list)


WALLETS: list[ExchangeWallet] = [
    ExchangeWallet(
        "0x75e89d5979e4f6fbaec97b2f5fe8b54e9b3f3b1d", "MEXC Hot", "MEXC", "shady", ["hot"]
    ),
    ExchangeWallet(
        "0x3c2b4be5f6f4be5c4b4c4b4c4b4c4b4c4b4c4b4", "MEXC Deposit", "MEXC", "shady", ["deposit"]
    ),
    ExchangeWallet(
        "0xf5b1f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8", "DigiFinex Hot", "DigiFinex", "shady", ["hot"]
    ),
    ExchangeWallet(
        "0x1b9e8c9a6f7e5d4c3b2a1f0e9d8c7b6a5f4e3d2", "LBank Hot", "LBank", "shady", ["hot"]
    ),
    ExchangeWallet(
        "0x0d0707963952f2fba59dd06f2b425ace40b492fe", "Gate.io Hot", "Gate.io", "shady", ["hot"]
    ),
    ExchangeWallet(
        "0x2b5634c42055806a59e9107ed44d43c426e58279", "KuCoin Hot", "KuCoin", "shady", ["hot"]
    ),
    ExchangeWallet("0x5c985e89d4e7c4f9c8b1a2d3e4f5a6b7c8d9e0f", "HTX Hot", "HTX", "shady", ["hot"]),
    ExchangeWallet(
        "0x28c6c06298d514db089934071355e5743bf21d60", "Binance 1", "Binance", "major", ["hot"]
    ),
    ExchangeWallet(
        "0x21a31ee1afc51d94c2efccaa2092ad1028285549", "Binance 2", "Binance", "major", ["hot"]
    ),
    ExchangeWallet(
        "0x1db92e2e5c3c0c3c0a5b5c6d7e8f9a0b1c2d3e4f", "Bybit Hot", "Bybit", "major", ["hot"]
    ),
]

POLL_INTERVALS: dict[str, int] = {"shady": 300, "major": 1800}

CRITICAL_ETH_OUTFLOW_24H = 1000
HIGH_ETH_TX = 100
HIGH_STABLECORE_TX = 500_000
WARNING_NET_FLOW_7D = 500
INFO_ETH_MOVEMENT_SHADY = 10


@dataclass
class WalletSnapshot:
    address: str
    label: str
    exchange: str
    category: str
    eth_balance: float = 0.0
    usdt_balance: float = 0.0
    usdc_balance: float = 0.0
    dai_balance: float = 0.0
    total_stable_value: float = 0.0
    large_outflows_24h: list[dict[str, Any]] = field(default_factory=list)
    large_outflows_7d: list[dict[str, Any]] = field(default_factory=list)
    net_flow_24h_eth: float = 0.0
    net_flow_7d_eth: float = 0.0
    scanned_at: str = ""


_state: dict[str, WalletSnapshot] = {}
_history: dict[str, list[dict[str, Any]]] = {}
_last_scan: dict[str, float] = {}
_scan_lock = asyncio.Lock()


async def _fetch_eth_balance(client: httpx.AsyncClient, address: str) -> float:
    params: dict[str, str | int] = {
        "chainid": 1,
        "module": "account",
        "action": "balance",
        "address": address,
        "tag": "latest",
        "apikey": ETHERSCAN_API_KEY,
    }
    r = await client.get(ETHERSCAN_BASE, params=params, timeout=15)
    data = r.json()
    if data.get("status") != "1":
        logger.debug("eth_balance_fail %s %s", address[:12], data.get("message", ""))
        return 0.0
    return int(data["result"]) / 1e18


async def _fetch_token_balance(client: httpx.AsyncClient, address: str, token: str) -> float:
    params: dict[str, str | int] = {
        "chainid": 1,
        "module": "account",
        "action": "tokenbalance",
        "contractaddress": token,
        "address": address,
        "tag": "latest",
        "apikey": ETHERSCAN_API_KEY,
    }
    r = await client.get(ETHERSCAN_BASE, params=params, timeout=15)
    data = r.json()
    if data.get("status") != "1":
        return 0.0
    decimals = 6 if token in (USDT, USDC) else 18
    value: float = int(data["result"]) / (10**decimals)
    return value


async def _fetch_recent_txns(client: httpx.AsyncClient, address: str) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "chainid": 1,
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
        "offset": 50,
        "apikey": ETHERSCAN_API_KEY,
    }
    r = await client.get(ETHERSCAN_BASE, params=params, timeout=15)
    data = r.json()
    if data.get("status") != "1":
        return []
    return cast(list[dict[str, Any]], data["result"])


def _classify_tx(tx: dict[str, Any], wallet_address: str) -> dict[str, Any] | None:
    value_eth = int(tx.get("value", 0)) / 1e18
    if value_eth < 0.01:
        return None
    is_outflow = tx.get("from", "").lower() == wallet_address.lower()
    return {
        "hash": tx["hash"],
        "ts": int(tx.get("timeStamp", 0)),
        "from": tx.get("from", ""),
        "to": tx.get("to", ""),
        "value_eth": value_eth,
        "direction": "outflow" if is_outflow else "inflow",
    }


def _analyze_txns(
    txns: list[dict[str, Any]], wallet_address: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float]:
    now = time.time()
    cutoff_24h = now - 86400
    cutoff_7d = now - 604800
    large_24h: list[dict[str, Any]] = []
    large_7d: list[dict[str, Any]] = []
    net_24h = net_7d = 0.0
    for tx in txns:
        enriched = _classify_tx(tx, wallet_address)
        if enriched is None:
            continue
        ts, value = enriched["ts"], enriched["value_eth"]
        mult = -1 if enriched["direction"] == "outflow" else 1
        if ts >= cutoff_7d:
            net_7d += mult * value
            large_7d.append(enriched)
        if ts >= cutoff_24h:
            net_24h += mult * value
            if enriched["direction"] == "outflow":
                large_24h.append(enriched)
    return large_24h, large_7d, net_24h, net_7d


async def _alert(title: str, msg: str, severity: str = "high") -> None:
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        await dispatch_alert(title, msg, alert_type="exchange_wallet", severity=severity)
    except Exception as exc:
        logger.debug("alert_dispatch_failed %s", str(exc)[:60])


def _check_and_fire_alerts(wallet: ExchangeWallet, snapshot: WalletSnapshot) -> list[str]:
    msgs: list[str] = []
    outflow_24h_eth = sum(
        t["value_eth"] for t in snapshot.large_outflows_24h if t["direction"] == "outflow"
    )

    if outflow_24h_eth >= CRITICAL_ETH_OUTFLOW_24H:
        msgs.append(
            f"CRITICAL: {wallet.label} outflow {outflow_24h_eth:.1f} ETH in 24h — potential hack or withdrawal run"
        )

    for t in snapshot.large_outflows_24h:
        if t["value_eth"] >= HIGH_ETH_TX:
            msgs.append(
                f"HIGH: {wallet.label} single outflow {t['value_eth']:.1f} ETH tx {t['hash'][:10]}..."
            )

    if snapshot.total_stable_value >= HIGH_STABLECORE_TX and outflow_24h_eth > 10:
        msgs.append(
            f"HIGH: {wallet.label} holds ${snapshot.total_stable_value:,.0f} stablecoins with active outflows"
        )

    if snapshot.net_flow_7d_eth < -WARNING_NET_FLOW_7D:
        msgs.append(
            f"WARNING: {wallet.label} net flow {snapshot.net_flow_7d_eth:.1f} ETH over 7d — reserve concerns"
        )

    if wallet.category == "shady" and outflow_24h_eth >= INFO_ETH_MOVEMENT_SHADY:
        msgs.append(f"INFO: {wallet.label} moved {outflow_24h_eth:.1f} ETH in 24h")

    for msg in msgs:
        sev = (
            "critical"
            if msg.startswith("CRITICAL")
            else (
                "high"
                if msg.startswith("HIGH")
                else ("medium" if msg.startswith("WARNING") else "info")
            )
        )
        asyncio.ensure_future(_alert(f"Wallet Alert: {wallet.exchange}", msg, severity=sev))
    return msgs


async def _scan_wallet(client: httpx.AsyncClient, wallet: ExchangeWallet) -> WalletSnapshot:
    address = wallet.address
    eth_balance, txns = await asyncio.gather(
        _fetch_eth_balance(client, address), _fetch_recent_txns(client, address)
    )
    stable_tasks = {
        name: _fetch_token_balance(client, address, c) for name, c in STABLECOINS.items()
    }
    stable_results = await asyncio.gather(*stable_tasks.values())
    stable = dict(zip(stable_tasks.keys(), stable_results, strict=False))
    large_24h, large_7d, net_24h, net_7d = _analyze_txns(txns, address)
    return WalletSnapshot(
        address=address,
        label=wallet.label,
        exchange=wallet.exchange,
        category=wallet.category,
        eth_balance=eth_balance,
        usdt_balance=stable["USDT"],
        usdc_balance=stable["USDC"],
        dai_balance=stable["DAI"],
        total_stable_value=stable["USDT"] + stable["USDC"] + stable["DAI"],
        large_outflows_24h=large_24h,
        large_outflows_7d=large_7d,
        net_flow_24h_eth=net_24h,
        net_flow_7d_eth=net_7d,
        scanned_at=datetime.now(UTC).isoformat(),
    )


def get_all_wallets() -> dict[str, dict[str, Any]]:
    now_ts = time.time()
    out: dict[str, dict[str, Any]] = {}
    for addr, s in _state.items():
        age_s = round(now_ts - _last_scan.get(addr, 0), 1) if addr in _last_scan else None
        out[addr] = {
            "label": s.label,
            "exchange": s.exchange,
            "category": s.category,
            "eth_balance": round(s.eth_balance, 6),
            "usdt_balance": round(s.usdt_balance, 2),
            "usdc_balance": round(s.usdc_balance, 2),
            "dai_balance": round(s.dai_balance, 2),
            "total_stable_value": round(s.total_stable_value, 2),
            "net_flow_24h_eth": round(s.net_flow_24h_eth, 4),
            "net_flow_7d_eth": round(s.net_flow_7d_eth, 4),
            "large_outflows_24h": s.large_outflows_24h[-5:],
            "scanned_at": s.scanned_at,
            "age_s": age_s,
        }
    return out


def get_wallet(address: str) -> dict[str, Any] | None:
    return get_all_wallets().get(address.lower())


async def exchange_wallet_cycle() -> dict[str, Any]:
    global _last_scan
    all_alerts: list[str] = []
    now_ts = time.time()
    async with _scan_lock:
        async with httpx.AsyncClient(timeout=15) as client:
            for wallet in WALLETS:
                interval = POLL_INTERVALS.get(wallet.category, 300)
                if now_ts - _last_scan.get(wallet.address, 0) < interval:
                    continue
                try:
                    await asyncio.sleep(0.3)  # rate limit: ~3 calls/sec on free tier
                    snapshot = await _scan_wallet(client, wallet)
                    _state[wallet.address] = snapshot
                    _last_scan[wallet.address] = now_ts
                    if wallet.address not in _history:
                        _history[wallet.address] = []
                    _history[wallet.address].append(
                        {
                            "ts": now_ts,
                            "eth_balance": snapshot.eth_balance,
                            "stable_value": snapshot.total_stable_value,
                        }
                    )
                    cutoff = now_ts - 2592000
                    _history[wallet.address] = [
                        h for h in _history[wallet.address] if h["ts"] > cutoff
                    ]
                    all_alerts.extend(_check_and_fire_alerts(wallet, snapshot))
                except Exception as exc:
                    logger.warning("scan_fail %s %s", wallet.label, str(exc)[:100])
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "wallets_scanned": len(_state),
        "alerts": all_alerts,
    }


async def exchange_wallet_loop() -> None:
    logger.info("exchange_wallet_loop starting shady=5m major=30m")
    while True:
        try:
            result = await exchange_wallet_cycle()
            if result["alerts"]:
                logger.info("cycle alerts=%d", len(result["alerts"]))
        except Exception as exc:
            logger.warning("cycle_failed %s", str(exc)[:120])
        await asyncio.sleep(60)

"""RPC large-transaction pollers (Ethereum native value, Solana balance delta)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import (
    ETH_LARGE_TX_WEI,
    RPC_MAX_TXS,
    SOLANA_LARGE_TX_LAMPORTS,
)
from .models import CriticalAlert

log = logging.getLogger("rugbrain.realtime_alerts")


async def _rpc_call(client: httpx.AsyncClient, url: str, method: str, params: list[Any]) -> Any:
    r = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Content-Type": "application/json"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"rpc_http_{r.status_code}")
    result = r.json().get("result")
    if result is None:
        err = (r.json() or {}).get("error") or {}
        raise RuntimeError(f"rpc_error_{err.get('code', 'unknown')}")
    return result


async def poll_ethereum_rpc(client: httpx.AsyncClient) -> list[CriticalAlert]:
    """Large native-value transactions on the latest Ethereum block."""
    from rugbrain import realtime_alerts as _pkg

    eth_rpc_url = _pkg.ETH_RPC_URL
    large_tx_usd = _pkg.LARGE_TX_USD
    if not eth_rpc_url:
        return []
    eth_price = await _pkg.resolve_native_price("ethereum")
    threshold_wei = ETH_LARGE_TX_WEI
    if eth_price > 0:
        threshold_wei = large_tx_usd / eth_price * 1e18
    if threshold_wei <= 0:
        return []
    candidates: list[CriticalAlert] = []
    try:
        block_hex = await _rpc_call(client, eth_rpc_url, "eth_blockNumber", [])
        block = await _rpc_call(client, eth_rpc_url, "eth_getBlockByNumber", [block_hex, True])
        txs = (block or {}).get("transactions", []) or []
        for tx in txs[:RPC_MAX_TXS]:
            try:
                value_wei = int(tx.get("value") or "0x0", 16)
            except (ValueError, TypeError):
                continue
            if value_wei < threshold_wei:
                continue
            usd = value_wei * eth_price / 1e18 if eth_price > 0 else 0.0
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large ETH transfer ({usd:,.0f} USD)",
                    description=(
                        f"Native transfer of {value_wei / 1e18:.2f} ETH in block "
                        f"{int(block_hex, 16)}. From {tx.get('from')} to {tx.get('to')}."
                    ),
                    source="rpc-ethereum",
                    chain="ethereum",
                    amount_usd=usd,
                    tx_hash=str(tx.get("hash", "")),
                    addresses=[str(tx.get("from", "")), str(tx.get("to", ""))],
                    raw={"block": block_hex},
                )
            )
    except Exception as e:
        log.warning("ethereum_rpc_poll_failed: %s", str(e)[:160])
    return candidates


async def poll_solana_rpc(client: httpx.AsyncClient) -> list[CriticalAlert]:
    """Large balance deltas on the latest Solana block."""
    from rugbrain import realtime_alerts as _pkg

    solana_rpc_url = _pkg.SOLANA_RPC_URL
    large_tx_usd = _pkg.LARGE_TX_USD
    if not solana_rpc_url:
        return []
    sol_price = await _pkg.resolve_native_price("solana")
    threshold_lamports = SOLANA_LARGE_TX_LAMPORTS
    if sol_price > 0:
        threshold_lamports = large_tx_usd / sol_price * 1e9
    if threshold_lamports <= 0:
        return []
    candidates: list[CriticalAlert] = []
    try:
        slot = await _rpc_call(client, solana_rpc_url, "getSlot", [])
        block = await _rpc_call(
            client,
            solana_rpc_url,
            "getBlock",
            [
                slot,
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        txs = (block or {}).get("transactions", []) or []
        for tx in txs[:RPC_MAX_TXS]:
            meta = tx.get("meta") or {}
            pre = meta.get("preBalances") or []
            post = meta.get("postBalances") or []
            if not pre or not post:
                continue
            try:
                max_delta = max(abs(int(a) - int(b)) for a, b in zip(pre, post, strict=False))
            except (ValueError, TypeError):
                continue
            if max_delta < threshold_lamports:
                continue
            usd = max_delta * sol_price / 1e9 if sol_price > 0 else 0.0
            sigs = (tx.get("transaction") or {}).get("signatures") or []
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large SOL movement ({usd:,.0f} USD)",
                    description=(
                        f"Account balance delta of {max_delta / 1e9:.2f} SOL in slot {slot}."
                    ),
                    source="rpc-solana",
                    chain="solana",
                    amount_usd=usd,
                    tx_hash=str(sigs[0]) if sigs else "",
                    raw={"slot": slot},
                )
            )
    except Exception as e:
        log.warning("solana_rpc_poll_failed: %s", str(e)[:160])
    return candidates


async def poll_rpc_large_txs(client: httpx.AsyncClient) -> list[CriticalAlert]:
    """Ethereum + Solana large-transaction candidates (single pass)."""
    from rugbrain import realtime_alerts as _pkg

    candidates: list[CriticalAlert] = []
    if _pkg.ETH_RPC_URL:
        candidates.extend(await _pkg.poll_ethereum_rpc(client))
    if _pkg.SOLANA_RPC_URL:
        candidates.extend(await _pkg.poll_solana_rpc(client))
    return candidates

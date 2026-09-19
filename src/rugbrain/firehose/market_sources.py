"""Market-awareness sources — fill the empty intelligence collections.

Audit 2026-08-25: whale_transfers, liquidation_events, token_unlocks,
stablecoin_market, exchange_flows, trending_snapshots, market_health,
social_intel, regulation_enforcement, hacks_v2 were ALL at 0 points while
the taxonomy promised total-market awareness. These adapters feed them
from FREE tier sources (no auth unless noted):

- CoinGecko trending + global      → trending_snapshots / market_health
- DefiLlama stablecoins + unlocks  → stablecoin_market / token_unlocks
- alternative.me FNG               → market_health (sentiment component)
- SEC press RSS                    → regulation_enforcement
- rekt.news RSS                    → hacks_v2
- Etherscan large ETH txs (key)    → whale_transfers

All doc shapes follow the firehose convention:
``{collection, content, metadata{source, ...}}`` — ingest-ready.
"""

from __future__ import annotations

import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger("rag.firehose.market")

_UA = {"User-Agent": "rugbrain-firehose/1.0 (+https://rugmunch.io)"}


def _now_iso() -> str:
    """ISO stamp so downstream freshness gates can rank intel docs."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _doc(collection: str, content: str, meta: dict[str, Any]) -> dict[str, Any]:
    meta = {"published": _now_iso(), **meta}
    return {"collection": collection, "content": content[:2000], "metadata": meta}


async def fetch_trending_snapshot() -> list[dict[str, Any]]:
    """CoinGecko trending coins → trending_snapshots."""
    try:
        async with httpx.AsyncClient(timeout=15, headers=_UA) as c:
            r = await c.get("https://api.coingecko.com/api/v3/search/trending")
        if r.status_code != 200:
            return []
        coins = r.json().get("coins", [])[:7]
        if not coins:
            return []
        names = [
            f"{x.get('item', {}).get('name', '?')} ({x.get('item', {}).get('symbol', '?')})"
            for x in coins
        ]
        return [
            _doc(
                "trending_snapshots",
                f"Trending tokens: {', '.join(names)}",
                {
                    "source": "coingecko_trending",
                    "type": "snapshot",
                    "tokens": [x.get("item", {}).get("symbol", "") for x in coins],
                },
            )
        ]
    except Exception as exc:
        logger.debug("trending snapshot failed: %s", exc)
        return []


async def fetch_market_health() -> list[dict[str, Any]]:
    """CoinGecko global metrics + Fear&Greed → market_health."""
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15, headers=_UA) as c:
            r = await c.get("https://api.coingecko.com/api/v3/global")
        if r.status_code == 200:
            d = r.json().get("data", {})
            mcap_t = d.get("total_market_cap", {}).get("usd", 0)
            mcap = f"{mcap_t / 1e12:.2f}T" if mcap_t > 1e12 else f"{mcap_t / 1e9:.0f}B"
            docs.append(
                _doc(
                    "market_health",
                    f"Global crypto market cap ${mcap}, BTC dominance "
                    f"{d.get('market_cap_percentage', {}).get('btc', 0):.1f}%, "
                    f"24h volume ${d.get('total_volume', {}).get('usd', 0) / 1e9:.1f}B, "
                    f"active coins {d.get('active_cryptocurrencies', '?')}",
                    {"source": "coingecko_global", "type": "daily_summary"},
                )
            )
    except Exception as exc:
        logger.debug("global metrics failed: %s", exc)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.alternative.me/fng/", params={"limit": 1})
        if r.status_code == 200:
            rows = r.json().get("data") or []
            if rows:
                docs.append(
                    _doc(
                        "market_health",
                        f"Fear & Greed Index: {rows[0].get('value')} "
                        f"({rows[0].get('value_classification')})",
                        {"source": "fear_greed", "type": "sentiment"},
                    )
                )
    except Exception as exc:
        logger.debug("fng failed: %s", exc)
    return docs


async def fetch_stablecoin_market() -> list[dict[str, Any]]:
    """DefiLlama stablecoins → stablecoin_market aggregate doc."""
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA) as c:
            r = await c.get("https://stablecoins.llama.fi/stablecoins?includePrices=false")
        if r.status_code != 200:
            return []
        assets = r.json().get("peggedAssets", [])
        top = sorted(
            (a for a in assets if isinstance(a.get("circulating"), dict)),
            key=lambda a: a["circulating"].get("peggedUSD", 0),
            reverse=True,
        )[:5]
        if not top:
            return []
        parts = [
            f"{a.get('name', '?')} ${a['circulating'].get('peggedUSD', 0) / 1e9:.1f}B" for a in top
        ]
        return [
            _doc(
                "stablecoin_market",
                f"Top stablecoins by circulation: {', '.join(parts)}",
                {
                    "source": "defillama_stablecoins",
                    "type": "snapshot",
                    "tokens": [a.get("symbol", "") for a in top],
                },
            )
        ]
    except Exception as exc:
        logger.debug("stablecoin pull failed: %s", exc)
        return []


async def fetch_derivatives_pressure() -> list[dict[str, Any]]:
    """CoinGecko derivatives exchanges by open interest → market_health.

    (Binance fapi is 451-geo-blocked from this host; Bybit 403s — CoinGecko's
    free derivatives endpoint carries the same signal. DefiLlama emissions
    went paid-tier, so token_unlocks stays empty for now.)
    """
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA) as c:
            r = await c.get(
                "https://api.coingecko.com/api/v3/derivatives/exchanges",
                params={"per_page": 5},
            )
        if r.status_code != 200:
            return []
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return []
        parts = [
            f"{x.get('name', '?')} {float(x.get('open_interest_btc') or 0):,.0f} BTC"
            for x in rows[:5]
        ]
        return [
            _doc(
                "market_health",
                f"Derivatives open interest leaders: {', '.join(parts)}",
                {"source": "coingecko_derivatives", "type": "snapshot"},
            )
        ]
    except Exception as exc:
        logger.debug("derivatives pull failed: %s", exc)
        return []


async def fetch_regulation_feed() -> list[dict[str, Any]]:
    """SEC press releases RSS → regulation_enforcement."""
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15, headers=_UA, follow_redirects=True) as c:
            r = await c.get("https://www.sec.gov/news/pressreleases.rss")
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)  # noqa: S314  # nosec B314 — trusted source (SEC RSS); stdlib ET by design (see rss_parser.py)
        for item in root.findall(".//item")[:5]:
            title = _txt(item.find("title"))
            if not title:
                continue
            docs.append(
                _doc(
                    "regulation_enforcement",
                    f"SEC: {title}. {_txt(item.find('description'))}",
                    {
                        "source": "sec_press",
                        "url": _txt(item.find("link")),
                        "published": _txt(item.find("pubDate")),
                        "category": "regulation",
                    },
                )
            )
    except Exception as exc:
        logger.debug("sec rss failed: %s", exc)
    return docs


def _txt(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


async def fetch_hack_feed() -> list[dict[str, Any]]:
    """DefiLlama hacks dataset → hacks_v2 (largest recent exploits)."""
    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA) as c:
            r = await c.get("https://api.llama.fi/hacks")
        if r.status_code != 200:
            return []
        rows = r.json()
        if not isinstance(rows, list):
            return []
        rows = sorted(
            (x for x in rows if isinstance(x.get("amount"), (int, float))),
            key=lambda x: x["amount"],
            reverse=True,
        )[:8]
        docs = []
        for x in rows:
            name = x.get("name", "?")
            amt = x.get("amount", 0)
            tech = x.get("technique", "")
            chains = ",".join(x.get("chain", []) or []) if isinstance(x.get("chain"), list) else ""
            docs.append(
                _doc(
                    "hacks_v2",
                    f"Exploit: {name} lost ${amt / 1e6:.1f}M "
                    f"({tech or 'unknown technique'})" + (f" on {chains}" if chains else ""),
                    {
                        "source": "defillama_hacks",
                        "category": "exploit",
                        "amount_usd": amt,
                        "technique": tech,
                        "chains": chains,
                    },
                )
            )
        return docs
    except Exception as exc:
        logger.debug("hacks pull failed: %s", exc)
        return []


_MULTICHAIN: tuple[tuple[int, str, str, float], ...] = (
    # (chainid, chain, native symbol, whale threshold in native units)
    (1, "ethereum", "ETH", 50),
    (8453, "base", "ETH", 50),
    (42161, "arbitrum", "ETH", 50),
    (10, "optimism", "ETH", 100),
    (56, "bsc", "BNB", 200),
    (137, "polygon", "POL", 20000),
    (43114, "avalanche", "AVAX", 500),
)
_BLOCKS_PER_CHAIN = 3


async def fetch_whale_transfers() -> list[dict[str, Any]]:
    """Large recent native transfers across SIX chains via Etherscan V2.

    One free key covers every chain (chainid param). Per-chain thresholds are
    honest floors for the post-L2 era; Whale-Alert-grade coverage needs
    streaming/paid sources. Paced at ~2.5 calls/sec against the 3/sec cap.
    """
    key = ""
    for cand in (
        os.getenv("ETHERSCAN_API_KEY_ALT", ""),
        os.getenv("ETHERSCAN_API_KEY_ALT2", ""),
        os.getenv("ETHERSCAN_API_KEY", ""),
    ):
        if cand and not cand.startswith(("$", "{")):
            key = cand
            break
    if not key:
        return []

    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            for chainid, chain, symbol, floor in _MULTICHAIN:
                await asyncio.sleep(0.4)
                rb = await c.get(
                    "https://api.etherscan.io/v2/api",
                    params={
                        "chainid": chainid,
                        "module": "proxy",
                        "action": "eth_blockNumber",
                        "apikey": key,
                    },
                )
                try:
                    latest = int(rb.json()["result"], 16)
                except (KeyError, ValueError, TypeError):
                    continue
                # _BLOCKS_PER_CHAIN per chain keeps the sweep bounded.
                for blk in range(max(0, latest - _BLOCKS_PER_CHAIN + 1), latest + 1):
                    await asyncio.sleep(0.4)
                    rb2 = await c.get(
                        "https://api.etherscan.io/v2/api",
                        params={
                            "chainid": chainid,
                            "module": "proxy",
                            "action": "eth_getBlockByNumber",
                            "tag": hex(blk),
                            "boolean": "true",
                            "apikey": key,
                        },
                    )
                    res = rb2.json().get("result")
                    if not isinstance(res, dict):
                        continue  # rate-limited / empty
                    for tx in res.get("transactions") or []:
                        try:
                            val = int(tx.get("value", "0x0"), 16) / 1e18
                        except (ValueError, TypeError):
                            continue
                        if val < floor:
                            continue
                        docs.append(
                            _doc(
                                "whale_transfers",
                                f"Whale transfer: {val:,.0f} {symbol} on {chain} "
                                f"(block {blk})",
                                {
                                    "source": "etherscan",
                                    "chain": chain,
                                    "hash": tx.get("hash", ""),
                                    "from": tx.get("from", ""),
                                    "to": tx.get("to", ""),
                                    "amount_native": round(val, 1),
                                    "symbol": symbol,
                                    "type": "large_transfer",
                                },
                            )
                        )
                if len(docs) >= 12:
                    break
    except Exception as exc:
        logger.debug("whale multichain pull failed: %s", exc)
    return docs[:12]


async def fetch_hyperliquid_positioning() -> list[dict[str, Any]]:
    """Hyperliquid whale positioning — OI concentration + extreme funding.

    Free public info API (no auth). Emits whale-positioning docs into
    whale_transfers (chain=hyperliquid): where the big money sits and which
    side pays funding.
    """
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA) as c:
            r = await c.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "metaAndAssetCtxs"},
            )
        if r.status_code != 200:
            return []
        payload = r.json()
        universe, ctxs = payload[0], payload[1]
        rows = []
        for meta, ctx in zip(universe.get("universe", []), ctxs, strict=False):
            name = meta.get("name", "")
            oi = float(ctx.get("openInterest", 0) or 0)
            funding = float(ctx.get("funding", 0) or 0) * 100
            mark = float(ctx.get("markPx", 0) or 0)
            if mark <= 0:
                continue
            rows.append((name, oi * mark, funding))
        if not rows:
            return []
        rows.sort(key=lambda x: x[1], reverse=True)
        top = rows[:6]
        parts = [
            f"{n} ${oi/1e6:.1f}M OI (funding {f:+.3f}%)" for n, oi, f in top
        ]
        hot = [f"{n} {f:+.3f}%" for n, _, f in rows if abs(f) >= 0.05][:4]
        content = (
            f"Hyperliquid whale positioning — largest OI: {'; '.join(parts)}"
            + (f". Extreme funding: {'; '.join(hot)}" if hot else "")
        )
        return [
            _doc(
                "whale_transfers",
                content,
                {
                    "source": "hyperliquid",
                    "chain": "hyperliquid",
                    "type": "positioning",
                },
            )
        ]
    except Exception as exc:
        logger.debug("hl positioning failed: %s", exc)
        return []




# ── Stablecoin whale transfers (USDT/USDC via eth_getLogs) ─────────────────
# The classic smart-money signal: big stablecoin mints/moves precede buying.
# Transfer event topic0; amount lives in data (uint256, 6 decimals for both).
_STABLES: tuple[tuple[str, str, str], ...] = (
    ("0xdAC17F958D2ee523a2206206994597C13D831ec7", "USDT", "ethereum"),
    ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "USDC", "ethereum"),
)
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_STABLE_WHALE_USD = 5_000_000


async def fetch_stablecoin_whales() -> list[dict[str, Any]]:
    """≥$5M USDT/USDC transfers on Ethereum via eth_getLogs (free key)."""
    key = ""
    for cand in (
        os.getenv("ETHERSCAN_API_KEY_ALT", ""),
        os.getenv("ETHERSCAN_API_KEY_ALT2", ""),
        os.getenv("ETHERSCAN_API_KEY", ""),
    ):
        if cand and not cand.startswith(("$", "{")):
            key = cand
            break
    if not key:
        return []
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            await asyncio.sleep(0.4)
            r = await c.get(
                "https://api.etherscan.io/v2/api",
                params={"chainid": 1, "module": "proxy", "action": "eth_blockNumber", "apikey": key},
            )
            latest = int(r.json()["result"], 16)
            for token_addr, symbol, _chain in _STABLES:
                await asyncio.sleep(0.4)
                rl = await c.get(
                    "https://api.etherscan.io/v2/api",
                    params={
                        "chainid": 1,
                        "module": "logs",
                        "action": "getLogs",
                        "address": token_addr,
                        "topic0": _TRANSFER_TOPIC,
                        "fromBlock": hex(max(0, latest - 40)),
                        "toBlock": hex(latest),
                        "apikey": key,
                    },
                )
                logs = rl.json().get("result")
                if not isinstance(logs, list):
                    continue
                for lg in logs:
                    try:
                        amount = int(lg.get("data", "0x0"), 16) / 1e6
                    except (ValueError, TypeError):
                        continue
                    if amount < _STABLE_WHALE_USD:
                        continue
                    topics = lg.get("topics", [])
                    frm = "0x" + topics[1][-40:] if len(topics) > 1 else ""
                    to = "0x" + topics[2][-40:] if len(topics) > 2 else ""
                    docs.append(
                        _doc(
                            "whale_transfers",
                            f"Stablecoin whale move: {amount/1e6:.1f}M {symbol} transferred",
                            {
                                "source": "etherscan_logs",
                                "chain": "ethereum",
                                "token": symbol,
                                "amount_usd": amount,
                                "from": frm,
                                "to": to,
                                "tx_hash": lg.get("transactionHash", ""),
                                "type": "stablecoin_whale",
                            },
                        )
                    )
    except Exception as exc:
        logger.debug("stablecoin whale pull failed: %s", exc)
    docs.sort(key=lambda d: d["metadata"].get("amount_usd", 0), reverse=True)
    return docs[:8]


# ── BTC whales via mempool.space (free, no auth) ────────────────────────────
_MEMPOOL = "https://mempool.space/api"
_BTC_WHALE_SATS = 20 * 100_000_000  # ≥20 BTC


async def fetch_btc_whales() -> list[dict[str, Any]]:
    """Large recent BTC transactions from mempool.space block feeds."""
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20, headers=_UA) as c:
            rb = await c.get(f"{_MEMPOOL}/v1/blocks")
            blocks = rb.json()[:5] if rb.status_code == 200 else []
            for blk in blocks:
                bh = blk.get("id", "")
                if not bh:
                    continue
                await asyncio.sleep(0.3)
                rt = await c.get(f"{_MEMPOOL}/block/{bh}/txs/0")
                if rt.status_code != 200:
                    continue
                for tx in rt.json():
                    val_btc = tx.get("value", 0) / 1e8
                    if val_btc < 20:
                        continue
                    docs.append(
                        _doc(
                            "whale_transfers",
                            f"BTC whale move: {val_btc:.1f} BTC in block {blk.get('height', '?')}",
                            {
                                "source": "mempool_space",
                                "chain": "bitcoin",
                                "txid": tx.get("txid", ""),
                                "amount_btc": round(val_btc, 1),
                                "type": "large_transfer",
                            },
                        )
                    )
                if len(docs) >= 8:
                    break
    except Exception as exc:
        logger.debug("btc whale pull failed: %s", exc)
    return docs[:8]


# ── Solana whales via public RPC block sampling ─────────────────────────────
# No free indexer covers SOL native transfers; we sample recent finalized
# blocks and diff pre/post balances per transaction. Bounded: 2 blocks/cycle.
_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_SOL_WHALE_LAMPORTS = 100 * 1_000_000_000  # ≥100 SOL native (sparse post-program era; ~$20k).
    # Upgrade path: Helius enhanced-txns webhooks for SPL-level whale feeds.


async def fetch_solana_whales() -> list[dict[str, Any]]:
    """Large SOL transfers sampled from recent finalized blocks (free RPC)."""
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=45, headers=_UA) as c:
            async def rpc(method: str, params: list[Any]) -> Any:
                r = await c.post(
                    _SOLANA_RPC,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                body = r.json()
                return body.get("result")

            slot = await rpc("getSlot", [{"commitment": "finalized"}])
            if not slot:
                return []
            for s in (slot - 3, slot - 4, slot - 5):
                await asyncio.sleep(0.5)
                blk = await rpc(
                    "getBlock",
                    [s, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                )
                if not isinstance(blk, dict):
                    continue
                txs = blk.get("transactions") or []
                for tx in txs:
                    meta = tx.get("meta") or {}
                    pre = meta.get("preBalances") or []
                    post = meta.get("postBalances") or []
                    if not pre or len(pre) != len(post):
                        continue
                    deltas = (p - q for p, q in zip(post, pre, strict=False))
                    biggest = max(deltas)
                    if biggest < _SOL_WHALE_LAMPORTS:
                        continue
                    docs.append(
                        _doc(
                            "whale_transfers",
                            f"SOL whale move: {biggest/1e9:,.0f} SOL received "
                            f"in slot {s}",
                            {
                                "source": "solana_rpc",
                                "chain": "solana",
                                "slot": s,
                                "amount_sol": round(biggest / 1e9, 1),
                                "type": "large_transfer",
                            },
                        )
                    )
                if len(docs) >= 6:
                    break
    except Exception as exc:
        logger.debug("solana whale pull failed: %s", exc)
    return docs[:6]

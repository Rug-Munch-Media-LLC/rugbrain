"""RugBrain market snapshot — at-a-glance market state + ClickHouse history.

``build_market_snapshot()`` aggregates free providers into one digest:

  * top gainers / losers (24h)      — CoinPaprika
  * most traded tokens              — CoinPaprika
  * newly listed tokens             — CoinPaprika
  * highest volume DEX pairs        — DexScreener (per tracked symbol)
  * TVL changes by chain            — DeFiLlama v2/chains
  * fear & greed index              — Alternative.me

Snapshots are stored in ClickHouse (``rag_market_snapshots``) for historical
analysis. Storage is best-effort: ``MarketSnapshotStore`` lazily imports
``clickhouse_connect`` (mirroring ``ClickHouseAlertLogger`` in alerts.py)
and degrades to a warning when the database is unreachable.

Usage::

    python -m rugbrain.market_snapshot --once
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain import market_data

log = logging.getLogger(__name__)

DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEFILLAMA_CHAINS_URL = "https://api.llama.fi/v2/chains"
FNG_URL = "https://api.alternative.me/fng/?limit=1"
HTTP_TIMEOUT = int(os.getenv("CRAWLER_HTTP_TIMEOUT", "30"))
USER_AGENT = "RMI-RAG/1.0 (+https://rugmunch.io; market-snapshot)"

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")
CLICKHOUSE_TABLE = os.getenv("CLICKHOUSE_SNAPSHOT_TABLE", "rag_market_snapshots")


# ── Source fetchers ──────────────────────────────────────────────────────


async def fetch_top_dex_pairs(
    client: httpx.AsyncClient, symbols: list[str], limit: int = 10
) -> list[dict[str, Any]]:
    """Highest 24h-volume DEX pairs among the most-traded tracked symbols."""
    pairs: list[dict[str, Any]] = []
    for symbol in symbols[:8]:
        try:
            r = await client.get(DEXSCREENER_SEARCH_URL, params={"q": symbol})
            if r.status_code != 200:
                continue
            for pair in (r.json() or {}).get("pairs") or []:
                if not isinstance(pair, dict):
                    continue
                pairs.append(
                    {
                        "pair": str(pair.get("pairAddress", "")),
                        "dex": str(pair.get("dexId") or ""),
                        "chain": str(pair.get("chainId") or ""),
                        "base": str(((pair.get("baseToken") or {}) or {}).get("symbol", "")),
                        "quote": str(((pair.get("quoteToken") or {}) or {}).get("symbol", "")),
                        "price_usd": float((pair.get("priceUsd") or 0) or 0),
                        "volume_24h_usd": float((pair.get("volume") or {}).get("h24") or 0),
                        "liquidity_usd": float((pair.get("liquidity") or {}).get("usd") or 0),
                        "url": str(pair.get("url", "")),
                    }
                )
        except Exception as e:
            log.debug("dexscreener_symbol_failed symbol=%s err=%s", symbol, str(e)[:120])
    unique: dict[str, dict[str, Any]] = {}
    for p in pairs:
        unique.setdefault(f"{p['chain']}:{p['pair']}", p)
    ranked = sorted(unique.values(), key=lambda p: p["volume_24h_usd"], reverse=True)
    return ranked[:limit]


async def fetch_tvl_changes(client: httpx.AsyncClient, limit: int = 12) -> list[dict[str, Any]]:
    """TVL per chain and 1-day change from DeFiLlama v2/chains."""
    try:
        r = await client.get(DEFILLAMA_CHAINS_URL)
        if r.status_code != 200:
            return []
        rows: list[dict[str, Any]] = []
        for chain in (r.json() or [])[:limit]:
            if not isinstance(chain, dict):
                continue
            rows.append(
                {
                    "chain": str(chain.get("name", "")),
                    "tvl": float(chain.get("tvl") or 0),
                    "change_1d": float(chain.get("change_1d") or 0),
                    "protocols": int(chain.get("protocols") or 0),
                }
            )
        return rows
    except Exception as e:
        log.warning("defillama_chains_error: %s", str(e)[:120])
        return []


async def fetch_fear_greed(client: httpx.AsyncClient) -> dict[str, Any]:
    """Numeric fear & greed value plus classification."""
    try:
        r = await client.get(FNG_URL)
        if r.status_code == 200:
            values = (r.json() or {}).get("data") or []
            if values:
                value = int(float(values[0].get("value") or 50))
                classification = "fearful" if value < 30 else "greedy" if value > 70 else "neutral"
                return {"value": value, "classification": classification}
    except Exception as e:
        log.debug("fng_failed err=%s", str(e)[:120])
    return {"value": 50, "classification": "neutral"}


# ── Snapshot builder ─────────────────────────────────────────────────────


async def build_market_snapshot(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Assemble the full at-a-glance market state digest."""
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        tickers = await market_data.fetch_coinpaprika_tickers(http)
        new_coins = await market_data.fetch_coinpaprika_new(http)
        most_traded = market_data.most_traded(tickers, 10)
        symbols = [t["symbol"] for t in most_traded]
        dex_pairs, tvl, fng = await asyncio.gather(
            fetch_top_dex_pairs(http, symbols),
            fetch_tvl_changes(http),
            fetch_fear_greed(http),
        )
    finally:
        if client is None:
            await http.aclose()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "top_gainers_24h": market_data.top_gainers(tickers, 10),
        "top_losers_24h": market_data.top_losers(tickers, 10),
        "most_traded_24h": most_traded,
        "newly_listed": new_coins,
        "top_dex_pairs": dex_pairs,
        "tvl_changes_by_chain": tvl,
        "fear_greed": fng,
    }


# ── ClickHouse storage ───────────────────────────────────────────────────


class MarketSnapshotStore:
    """Best-effort ClickHouse logger for market snapshots (lazy import)."""

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        snapshot_at DateTime('UTC'),
        top_gainers String,
        top_losers String,
        most_traded String,
        newly_listed String,
        top_dex_pairs String,
        tvl_changes String,
        fear_greed_value Int8,
        fear_greed_class String,
        raw String
    ) ENGINE = MergeTree()
    ORDER BY (snapshot_at)
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._db: str | None = None

    def _get(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            ch = importlib.import_module("clickhouse_connect")
            client = ch.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                interface=CLICKHOUSE_INTERFACE,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DATABASE,
            )
            for db in (CLICKHOUSE_DATABASE, CLICKHOUSE_FALLBACK_DATABASE):
                try:
                    client.command(self.DDL.format(db=db, table=CLICKHOUSE_TABLE))
                    self._db = db
                    break
                except Exception:
                    client = None
            if self._db is None:
                raise RuntimeError("no writable clickhouse database for market snapshots")
            self._client = client
            log.info("clickhouse_market_snapshots_ready table=%s db=%s", CLICKHOUSE_TABLE, self._db)
        except Exception as e:
            log.warning("clickhouse_market_snapshots_unavailable: %s", str(e)[:120])
            self._client = False
        return self._client if self._client is not False else None

    def store(self, snapshot: dict[str, Any]) -> bool:
        client = self._get()
        if client is None or self._db is None:
            return False
        fng = snapshot.get("fear_greed") or {}
        try:
            client.insert(
                f"{self._db}.{CLICKHOUSE_TABLE}",
                [
                    [
                        snapshot.get("generated_at", ""),
                        json.dumps(snapshot.get("top_gainers_24h", []), default=str),
                        json.dumps(snapshot.get("top_losers_24h", []), default=str),
                        json.dumps(snapshot.get("most_traded_24h", []), default=str),
                        json.dumps(snapshot.get("newly_listed", []), default=str),
                        json.dumps(snapshot.get("top_dex_pairs", []), default=str),
                        json.dumps(snapshot.get("tvl_changes_by_chain", []), default=str),
                        int(fng.get("value", 50)),
                        str(fng.get("classification", "neutral")),
                        json.dumps(snapshot, default=str)[:10000],
                    ]
                ],
                column_names=[
                    "snapshot_at",
                    "top_gainers",
                    "top_losers",
                    "most_traded",
                    "newly_listed",
                    "top_dex_pairs",
                    "tvl_changes",
                    "fear_greed_value",
                    "fear_greed_class",
                    "raw",
                ],
            )
            return True
        except Exception as e:
            log.warning("clickhouse_market_snapshot_insert_failed: %s", str(e)[:120])
            return False

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        client = self._get()
        if client is None or self._db is None:
            return []
        try:
            rows = client.query(
                # db/table are env-config constants (never user input); limit is an int.
                f"SELECT snapshot_at, fear_greed_value, fear_greed_class, top_gainers "  # noqa: S608  # nosec B608
                f"FROM {self._db}.{CLICKHOUSE_TABLE} ORDER BY snapshot_at DESC LIMIT {limit}"
            )
            return [dict(zip(rows.column_names, row, strict=True)) for row in rows.result_rows]
        except Exception as e:
            log.warning("clickhouse_market_snapshot_query_failed: %s", str(e)[:120])
            return []


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RugBrain market snapshot")
    parser.add_argument("--once", action="store_true", help="build + store one snapshot")
    parser.add_argument("--dry-run", action="store_true", help="build only, don't store")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    async def run() -> None:
        snapshot = await build_market_snapshot()
        print(json.dumps(snapshot, default=str)[:2000])
        if args.dry_run:
            return
        store = MarketSnapshotStore()
        ok = store.store(snapshot)
        print(f"MARKET SNAPSHOT STORED: {ok}")

    asyncio.run(run())


if __name__ == "__main__":
    _main()


__all__ = [
    "MarketSnapshotStore",
    "build_market_snapshot",
    "fetch_fear_greed",
    "fetch_top_dex_pairs",
    "fetch_tvl_changes",
]

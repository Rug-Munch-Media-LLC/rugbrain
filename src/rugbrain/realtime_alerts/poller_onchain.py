"""ClickHouse on-chain anomaly poller (DEX whale / fresh address / oracle)."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from .config import (
    CLICKHOUSE_DATABASE,
    CLICKHOUSE_FALLBACK_DATABASE,
    CLICKHOUSE_HOST,
    CLICKHOUSE_INTERFACE,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_PORT,
    CLICKHOUSE_USER,
    LARGE_TX_USD,
    NEW_ADDRESS_MAX_TXS,
    ONCHAIN_LOOKBACK_MINUTES,
    ORACLE_VOLUME_USD,
    ORACLE_WHALE_MIN,
)
from .models import CriticalAlert

log = logging.getLogger("rugbrain.realtime_alerts")

_DEX_WHALE_SQL = """
SELECT chain, block_timestamp, tx_hash, dex, contract_address AS pair,
       token0, token1, sender, recipient, volume_usd
FROM __DB__.dex_events
WHERE volume_usd >= {threshold:Float64}
  AND block_timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
ORDER BY volume_usd DESC
LIMIT 10
"""

_NEW_ADDRESS_SQL = """
SELECT d.chain, d.block_timestamp, d.tx_hash, d.dex, d.contract_address AS pair,
       d.token0, d.token1, d.sender, d.recipient, d.volume_usd
FROM __DB__.dex_events d
WHERE d.volume_usd >= {threshold:Float64}
  AND d.block_timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
  AND (
    SELECT count()
    FROM __DB__.dex_events h
    WHERE h.recipient = d.recipient AND h.block_timestamp < d.block_timestamp
  ) <= {new_max:UInt32}
ORDER BY d.volume_usd DESC
LIMIT 10
"""

_ORACLE_PATTERN_SQL = """
SELECT chain, contract_address AS pair, token0, token1,
       count() AS whale_swaps,
       sum(volume_usd) AS cum_volume_usd,
       max(block_timestamp) AS last_seen
FROM __DB__.dex_events
WHERE whale = 1
  AND block_timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
GROUP BY chain, contract_address, token0, token1
HAVING count() >= {whale_min:UInt32}
   AND sum(volume_usd) >= {volume_usd:Float64}
ORDER BY cum_volume_usd DESC
LIMIT 10
"""


def _dex_whale_sql(db: str) -> str:
    # db is operator config (CLICKHOUSE_DATABASE env), never user input.
    return _DEX_WHALE_SQL.replace("__DB__", db)


def _new_address_sql(db: str) -> str:
    # Fresh-recipient whale swaps: recipient has few prior ledger rows.
    return _NEW_ADDRESS_SQL.replace("__DB__", db)


def _oracle_pattern_sql(db: str) -> str:
    # Oracle-manipulation proxy: a single pair sustaining many whale swaps
    # in a short window implies price-impact abuse.
    return _ORACLE_PATTERN_SQL.replace("__DB__", db)


async def poll_onchain_anomalies(
    ch: Any | None = None,
    db: str | None = None,
) -> list[CriticalAlert]:
    """Query ClickHouse for large transfers / DEX whale / oracle patterns.

    Returns candidate alerts; ``[]`` when ClickHouse is unreachable or the
    indexer tables do not exist yet.
    """
    candidates: list[CriticalAlert] = []
    try:
        if ch is None:
            ch_mod = importlib.import_module("clickhouse_connect")
            ch = ch_mod.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                interface=CLICKHOUSE_INTERFACE,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DATABASE,
            )
        db = db or CLICKHOUSE_FALLBACK_DATABASE
        params = {
            "threshold": LARGE_TX_USD,
            "minutes": ONCHAIN_LOOKBACK_MINUTES,
            "new_max": NEW_ADDRESS_MAX_TXS,
            "whale_min": ORACLE_WHALE_MIN,
            "volume_usd": ORACLE_VOLUME_USD,
        }
        settings = {"max_execution_time": 20}

        rows = ch.query(_dex_whale_sql(db), parameters=params, settings=settings).result_rows
        for row in rows:
            chain, _ts, tx_hash, dex, pair, t0, t1, sender, recipient, vol = row
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large DEX swap on {chain} ({dex})",
                    description=(
                        f"Whale swap {vol:,.0f} USDC-equivalent on {chain} "
                        f"pair {pair} (tokens {t0}/{t1}). Sender {sender} -> recipient {recipient}."
                    ),
                    source="clickhouse-dex",
                    chain=str(chain),
                    amount_usd=float(vol),
                    tx_hash=str(tx_hash),
                    addresses=[str(sender), str(recipient)],
                    raw={"pair": str(pair), "dex": str(dex)},
                )
            )

        rows = ch.query(_new_address_sql(db), parameters=params, settings=settings).result_rows
        for row in rows:
            chain, _ts, tx_hash, dex, pair, t0, t1, sender, recipient, vol = row
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large transfer to new address on {chain}",
                    description=(
                        f"{vol:,.0f} USDC-equivalent moved to a fresh recipient "
                        f"{recipient} (pair {pair}, {dex})."
                    ),
                    source="clickhouse-new-address",
                    chain=str(chain),
                    amount_usd=float(vol),
                    tx_hash=str(tx_hash),
                    addresses=[str(sender), str(recipient)],
                    raw={"pair": str(pair), "dex": str(dex)},
                )
            )

        rows = ch.query(_oracle_pattern_sql(db), parameters=params, settings=settings).result_rows
        for row in rows:
            chain, pair, t0, t1, swaps, cum, _last = row
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Oracle-manipulation pattern on {chain} pair {pair}",
                    description=(
                        f"{swaps} whale swaps totalling {cum:,.0f} USDC-equivalent "
                        f"on {chain} {pair} in {ONCHAIN_LOOKBACK_MINUTES} minutes — "
                        "possible price-impact abuse."
                    ),
                    source="clickhouse-oracle",
                    chain=str(chain),
                    amount_usd=float(cum),
                    raw={"pair": str(pair), "whale_swaps": int(swaps), "tokens": f"{t0}/{t1}"},
                )
            )
    except Exception as e:
        log.warning("onchain_anomaly_poll_failed: %s", str(e)[:200])
    return candidates

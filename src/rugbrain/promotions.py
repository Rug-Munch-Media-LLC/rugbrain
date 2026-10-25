"""DexScreener paid-promotion model + query layer (single source of truth).

Real schema observed 2026-10-25 (nothing invented, nothing normalised away):

  token-boosts/top/v1      url, chainId, tokenAddress, description, icon, header,
                           openGraph, links[], totalAmount        (no `amount`, NO timestamp)
  token-boosts/latest/v1   same + totalAmount + amount             (NO timestamp)
  token-profiles/latest/v1 url, chainId, tokenAddress, description, icon, header,
                           openGraph, links[], cto                 (NO amount, NO timestamp)

`totalAmount`/`amount` are the USD paid for the promotion. A surface that exposes no
amount yields ``amount_usd=None`` -- never 0. Upstream exposes no refund/status field,
so refunds cannot be modelled here [REVIEW].
"""

from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime
from typing import Any

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")
TABLE = os.getenv("CLICKHOUSE_PROMO_TABLE", "dm_promotions")

# ORDER BY (chain, token_address, kind) keeps one live row per promotion surface;
# last_seen is the ReplacingMergeTree version, so a re-run replaces, never duplicates.
DDL = """
CREATE TABLE IF NOT EXISTS {db}.{table} (
    chain LowCardinality(String),
    token_address String,
    url String,
    kind LowCardinality(String),
    amount_usd Nullable(Float64),
    paid UInt8,
    first_seen DateTime('UTC'),
    last_seen DateTime('UTC'),
    source LowCardinality(String)
) ENGINE = ReplacingMergeTree(last_seen)
ORDER BY (chain, token_address, kind)
"""

COLUMNS = ["chain", "token_address", "url", "kind", "amount_usd", "paid",
           "first_seen", "last_seen", "source"]


def _amount(item: dict[str, Any]) -> float | None:
    """Paid amount in USD, or None when the surface exposes no amount field."""
    raw = item.get("amount") if item.get("amount") is not None else item.get("totalAmount")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_payload(items: list[dict[str, Any]], kind: str, source: str,
                  seen_at: datetime | None = None) -> list[dict[str, Any]]:
    """Normalise one endpoint payload into dm_promotions rows."""
    stamp = seen_at or datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for item in items:
        chain = str(item.get("chainId") or "").strip()
        token = str(item.get("tokenAddress") or "").strip()
        if not chain or not token:
            continue
        row_kind = "cto" if kind == "profile" and item.get("cto") is True else kind
        out.append({
            "chain": chain,
            "token_address": token,
            "url": str(item.get("url") or ""),
            "kind": row_kind,
            "amount_usd": _amount(item),
            "paid": 1,
            "first_seen": stamp,
            "last_seen": stamp,
            "source": source,
        })
    return out


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (chain, token, kind), newest last_seen wins (idempotent re-run)."""
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["chain"], row["token_address"], row["kind"])
        cur = best.get(key)
        if cur is None or row["last_seen"] >= cur["last_seen"]:
            if cur is not None and cur["first_seen"] < row["first_seen"]:
                row = {**row, "first_seen": cur["first_seen"]}
            best[key] = row
    return list(best.values())


def paid_only_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str], bool]:
    """paid_only=True when a token is seen ONLY through a bought boost."""
    kinds: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        kinds.setdefault((row["chain"], row["token_address"]), set()).add(row["kind"])
    return {key: value == {"boost"} for key, value in kinds.items()}


def spend_by_token(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    spend: dict[tuple[str, str], float] = {}
    for row in rows:
        if row["amount_usd"] is None:
            continue
        key = (row["chain"], row["token_address"])
        spend[key] = spend.get(key, 0.0) + row["amount_usd"]
    return spend


def spend_by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
    daily: dict[str, float] = {}
    for row in rows:
        if row["amount_usd"] is None:
            continue
        day = row["last_seen"].strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + row["amount_usd"]
    return daily


def client() -> Any:
    """ClickHouse client, lazily imported; first database that accepts the DDL wins."""
    ch = importlib.import_module("clickhouse_connect")
    cli = ch.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT,
                        interface=CLICKHOUSE_INTERFACE, username=CLICKHOUSE_USER,
                        password=CLICKHOUSE_PASSWORD, database=CLICKHOUSE_DATABASE)
    for db in (CLICKHOUSE_DATABASE, CLICKHOUSE_FALLBACK_DATABASE):
        try:
            cli.command(DDL.format(db=db, table=TABLE))
            return cli
        except Exception:  # noqa: BLE001 - only CREATE permission differs per database
            continue
    raise RuntimeError("no writable clickhouse database for dm_promotions")


def _qualified() -> str:
    return f"{CLICKHOUSE_FALLBACK_DATABASE}.{TABLE}"


def top_paid_promotions(days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
    """Top paid promotions in the window, with amount and chain (agent/API surface)."""
    sql = f"""
    SELECT chain, token_address, argMax(url, last_seen) AS url,
           sum(coalesce(amount_usd, 0)) AS amount_usd,
           count() AS surfaces, max(last_seen) AS last_seen
    FROM {_qualified()} FINAL
    WHERE last_seen >= now() - INTERVAL {{days:UInt32}} DAY
    GROUP BY chain, token_address ORDER BY amount_usd DESC LIMIT {{limit:UInt32}}
    """
    res = client().query(sql, parameters={"days": days, "limit": limit})
    return [dict(zip(res.column_names, row, strict=True)) for row in res.result_rows]


def promotions_for(chain: str, token_address: str) -> dict[str, Any]:
    """Promotion history + paid_only flag for one token (trending-algo input)."""
    sql = f"""
    SELECT kind, url, amount_usd, paid, first_seen, last_seen, source
    FROM {_qualified()} FINAL
    WHERE chain = {{chain:String}} AND token_address = {{token:String}}
    ORDER BY last_seen DESC
    """
    res = client().query(sql, parameters={"chain": chain, "token": token_address})
    rows = [dict(zip(res.column_names, row, strict=True)) for row in res.result_rows]
    kinds = {row["kind"] for row in rows}
    return {"chain": chain, "token_address": token_address, "rows": rows,
            "spend_usd": sum(row["amount_usd"] or 0 for row in rows),
            "paid_only": bool(rows) and kinds == {"boost"}}

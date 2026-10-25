#!/usr/bin/env python3
"""Collect DexScreener paid promotions (boosts + profiles) into ClickHouse.

Every surface on this API is bought visibility, so every row is paid=1. Amounts come
from `amount` (latest) or `totalAmount` (top); profiles expose neither -> NULL.
Run: python3 scripts/collect_promotions.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from rugbrain.promotions import (CLICKHOUSE_FALLBACK_DATABASE, COLUMNS, TABLE,
                                 client, dedupe_rows, parse_payload, spend_by_day,
                                 spend_by_token)

TIMEOUT = 10
ATTEMPTS = 2  # one retry, per contract
ENDPOINTS = (
    ("https://api.dexscreener.com/token-boosts/top/v1", "boost", "dexscreener:token-boosts/top"),
    ("https://api.dexscreener.com/token-boosts/latest/v1", "boost", "dexscreener:token-boosts/latest"),
    ("https://api.dexscreener.com/token-profiles/latest/v1", "profile", "dexscreener:token-profiles/latest"),
)


class PromotionFetchError(RuntimeError):
    """Upstream unreachable, non-JSON, or not a list after the retry."""


def fetch(url: str) -> list[dict]:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rugbrain-promotions/1"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise PromotionFetchError(f"{url}: unexpected payload {type(payload).__name__}")
            return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt + 1 < ATTEMPTS:
                time.sleep(2)
    raise PromotionFetchError(f"{url}: {last}") from last


def existing_first_seen(cli, rows: list[dict]) -> dict[tuple[str, str, str], datetime]:
    """Preserve true first sighting across re-runs (ReplacingMergeTree keeps last_seen)."""
    addrs = sorted({row["token_address"] for row in rows})
    if not addrs:
        return {}
    sql = f"""SELECT chain, token_address, kind, min(first_seen)
              FROM {CLICKHOUSE_FALLBACK_DATABASE}.{TABLE}
              WHERE token_address IN {{addrs:Array(String)}}
              GROUP BY chain, token_address, kind"""
    res = cli.query(sql, parameters={"addrs": addrs})
    return {(chain, token, kind): seen for chain, token, kind, seen in res.result_rows}


def main() -> int:
    seen = datetime.now(UTC)
    rows: list[dict] = []
    for url, kind, source in ENDPOINTS:
        rows.extend(parse_payload(fetch(url), kind, source, seen))
    rows = dedupe_rows(rows)
    cli = client()
    prior = existing_first_seen(cli, rows)
    for row in rows:
        row["first_seen"] = prior.get(
            (row["chain"], row["token_address"], row["kind"]), row["first_seen"])
    cli.insert(f"{CLICKHOUSE_FALLBACK_DATABASE}.{TABLE}",
               [[row[name] for name in COLUMNS] for row in rows], column_names=COLUMNS)
    spend = spend_by_token(rows)
    top = sorted(spend.items(), key=lambda kv: kv[1], reverse=True)[:5]
    print(json.dumps({
        "rows_upserted": len(rows),
        "tokens_with_amount": len(spend),
        "top_spend": [{"chain": c, "token": t, "usd": round(v, 2)} for (c, t), v in top],
        "spend_by_day": spend_by_day(rows),
    }, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PromotionFetchError as exc:
        print(f"FETCH FAILED: {exc}", file=sys.stderr)
        sys.exit(2)

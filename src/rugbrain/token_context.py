"""Rugbrain token context: one mint -> launch trajectory + nearby hacks + sanctions.

Answers the three questions the market datasets exist for:
  (a) do we hold a pump.fun launch + intra-window price path for this mint?
  (b) which hacks hit this chain around the launch date?
  (c) is the mint (or anything sharing the address string) on the OFAC SDN list?

All three reads hit wallet_memory tables ingested by scripts/ingest_*_ch.py.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

HACK_WINDOW_DAYS = 90
HACK_LIMIT = 5


def _chain_of(mint: str) -> str:
    if mint.startswith("0x") and len(mint) == 42:
        return "Ethereum"
    if mint.lower().endswith("pump") or 32 <= len(mint) <= 44:
        return "Solana"
    return "unknown"


def token_context(mint: str, client: Any = None, anchor: date | None = None) -> dict[str, Any]:
    """Return launch trajectory, nearest hacks and sanctions match for ``mint``."""
    if client is None:
        from rugbrain.clickhouse_store import get_client, load_env_rag

        load_env_rag()
        client = get_client(database="wallet_memory")

    launch = client.query(
        "SELECT mint, seen_at, creator, symbol, name, market_cap_sol, creator_pct, "
        "rugcheck_score, total_holders, insiders_detected, top20_pct, rugged "
        "FROM wallet_memory.pumpfun_launches WHERE mint = %(mint)s LIMIT 1",
        parameters={"mint": mint},
    )
    trajectory = client.query(
        "SELECT launched_at, observed_at, price_launch, price_now, price_change_pct, p25, "
        "p50, p75, from_peak_pct, slope_pct, graduated FROM wallet_memory.pumpfun_observations "
        "WHERE mint = %(mint)s ORDER BY observed_at",
        parameters={"mint": mint},
    )
    verdict = client.query(
        "SELECT decided_at, model, decision, change_after_pct, peak_pct FROM "
        "wallet_memory.pumpfun_ai_verdicts WHERE mint = %(mint)s ORDER BY decided_at LIMIT 1",
        parameters={"mint": mint},
    )
    launch_date = launch.result_rows[0][1].date() if launch.result_rows else None
    observed = trajectory.result_rows[0][0].date() if trajectory.result_rows else None
    anchor = anchor or launch_date or observed or date.today()
    chain = _chain_of(mint)
    hacks = client.query(
        "SELECT hack_date, name, classification, technique, amount, chain FROM wallet_memory.hacks "
        "WHERE has(chain, %(chain)s) AND hack_date BETWEEN %(lo)s AND %(hi)s "
        "ORDER BY abs(dateDiff('day', hack_date, toDate(%(anchor)s))) LIMIT %(limit)s",
        parameters={
            "chain": chain,
            "lo": anchor - timedelta(days=HACK_WINDOW_DAYS),
            "hi": anchor + timedelta(days=HACK_WINDOW_DAYS),
            "anchor": anchor,
            "limit": HACK_LIMIT,
        },
    )
    sanctions = client.query(
        "SELECT address, chain, name, program, sdn_type FROM wallet_memory.sanctions_crypto "
        "WHERE address_lower = %(addr)s",
        parameters={"addr": mint.lower()},
    )
    return {
        "mint": mint,
        "chain": chain,
        "anchor": anchor.isoformat(),
        "launch": dict(zip(launch.column_names, launch.result_rows[0], strict=False)) if launch.result_rows else None,
        "trajectory": [dict(zip(trajectory.column_names, r, strict=False)) for r in trajectory.result_rows],
        "verdict": dict(zip(verdict.column_names, verdict.result_rows[0], strict=False)) if verdict.result_rows else None,
        "nearby_hacks": [dict(zip(hacks.column_names, r, strict=False)) for r in hacks.result_rows],
        "sanctions": [dict(zip(sanctions.column_names, r, strict=False)) for r in sanctions.result_rows],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 -m rugbrain.token_context <mint>")
    print(json.dumps(token_context(sys.argv[1]), indent=1, default=str))

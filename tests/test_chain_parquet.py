"""Tests for chain-indexer parquet + DuckDB integration (chain_parquet.py).

Covers:
  - read-only SQL guard shared with parquet_analytics
  - chain prefix / date / chain resolution helpers
  - run_chain_query over local parquet files (transfers + dex views)
  - intent detection + canned SQL for the search fallback

Self-contained: writes parquet to temp files and queries them with DuckDB
locally — no B2, rclone, or network required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rugbrain.chain_parquet import (
    build_intent_sql,
    chain_prefix,
    detect_chain_data_intent,
    extract_chain_from_query,
    extract_date_from_query,
    run_chain_query,
)
from rugbrain.parquet_analytics import validate_read_only_sql

# ── Fixtures ──────────────────────────────────────────────────────────


def _write_chain_parquet(tmp_path: Path) -> list[Path]:
    """Write transfers + dex parquet files matching the indexer schema."""
    import duckdb

    con = duckdb.connect()
    # Transfers file — column layout matches {chain}_{date}.parquet
    transfers = [
        {
            "chain": "gnosis",
            "block_number": 47526606,
            "tx_hash": f"0xtransfer{i}",
            "from_address": "0x0000000000000000000000000000000000000000" if i == 1 else "0xaa",
            "to_address": "0xbb",
            "token_address": f"0xtoken{i}",
            "value": "1000000000000000000",
            "block_timestamp": f"2026-08-03 0{i}:00:00+00",
            "log_index": i,
            "token_symbol": "TST",
            "token_name": "Test",
            "ingested_at": "2026-08-03 10:00:00+00",
            "source": "subsquid",
        }
        for i in range(1, 4)
    ]
    transfers_path = tmp_path / "gnosis_2026-08-03.parquet"
    con.execute(
        "COPY (SELECT * FROM (VALUES "  # noqa: S608
        + ",".join(
            f"('{t['chain']}', {t['block_number']}, '{t['tx_hash']}', "
            f"'{t['from_address']}', '{t['to_address']}', '{t['token_address']}', "
            f"'{t['value']}', '{t['block_timestamp']}'::TIMESTAMPTZ, {t['log_index']}, "
            f"'{t['token_symbol']}', '{t['token_name']}', '{t['ingested_at']}'::TIMESTAMPTZ, "
            f"'{t['source']}')"
            for t in transfers
        )
        + ") AS t(chain, block_number, tx_hash, from_address, to_address, "
        "token_address, value, block_timestamp, log_index, token_symbol, "
        "token_name, ingested_at, source)) "
        f"TO '{transfers_path}' (FORMAT PARQUET)"
    )

    # Dex file — column layout matches {chain}_dex_{date}.parquet
    dex = [
        {
            "type": "swap",
            "chain": "gnosis",
            "block_number": 47526608,
            "block_timestamp": "2026-08-03 02:00:10+00",
            "tx_hash": "0xswap1",
            "log_index": 18,
            "contract_address": "0xcontract1",
            "dex": "uniswap_v3",
            "version": "v3",
            "token0": "0xtoken0",
            "token1": "0xtoken1",
            "sender": "0xfrom",
            "recipient": "0xto",
            "amount0_in": "0",
            "amount1_in": "499999999365328626",
            "amount0_out": "0",
            "amount1_out": "499999999365328626",
            "amount0": "115792089237316195423570985008687907853269984665640564039457584007913129639935",
            "amount1": "499999999365328626",
            "whale": 0,
            "ingested_at": "2026-08-03 10:14:55+00",
            "source": "lite_indexer",
        }
    ]
    dex_path = tmp_path / "gnosis_dex_2026-08-03.parquet"
    con.execute(
        "COPY (SELECT * FROM (VALUES "  # noqa: S608
        + ",".join(
            f"('{d['type']}', '{d['chain']}', {d['block_number']}, "
            f"'{d['block_timestamp']}'::TIMESTAMPTZ, '{d['tx_hash']}', {d['log_index']}, "
            f"'{d['contract_address']}', '{d['dex']}', '{d['version']}', "
            f"'{d['token0']}', '{d['token1']}', '{d['sender']}', '{d['recipient']}', "
            f"'{d['amount0_in']}', '{d['amount1_in']}', '{d['amount0_out']}', "
            f"'{d['amount1_out']}', '{d['amount0']}', '{d['amount1']}', {d['whale']}, "
            f"'{d['ingested_at']}'::TIMESTAMPTZ, '{d['source']}')"
            for d in dex
        )
        + ") AS t(type, chain, block_number, block_timestamp, tx_hash, log_index, "
        "contract_address, dex, version, token0, token1, sender, recipient, "
        "amount0_in, amount1_in, amount0_out, amount1_out, amount0, amount1, "
        "whale, ingested_at, source)) "
        f"TO '{dex_path}' (FORMAT PARQUET)"
    )
    con.close()
    return [transfers_path, dex_path]


# ── Prefix / date / chain resolution ──────────────────────────────────


def test_chain_prefix_basic():
    assert chain_prefix("ethereum") == "b2-crypt:chain-data/parquet/ethereum"
    assert (
        chain_prefix("gnosis", "2026-08-03", "00")
        == "b2-crypt:chain-data/parquet/gnosis/2026/08/03/00"
    )
    assert chain_prefix("base", "2026-08-03") == "b2-crypt:chain-data/parquet/base/2026/08/03"


def test_chain_prefix_bad_date():
    # Invalid dates are skipped rather than crashing.
    assert chain_prefix("eth", "not-a-date") == "b2-crypt:chain-data/parquet/eth"


def test_extract_chain_from_query():
    assert extract_chain_from_query("show me recent ETH transfers") == "ethereum"
    assert extract_chain_from_query("who deployed tokens on Base yesterday") == "base"
    assert extract_chain_from_query("solana whale activity") == "solana"
    assert extract_chain_from_query("reentrancy exploit on arbitrum") == "arbitrum"
    assert extract_chain_from_query("nothing here") is None


def test_extract_date_from_query():
    assert (
        extract_date_from_query("activity yesterday")
        == (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    )
    assert extract_date_from_query("activity on 2026-08-03") == "2026-08-03"
    assert extract_date_from_query("activity on 2026/08/03") == "2026-08-03"
    assert extract_date_from_query("nothing here") is None


def test_detect_chain_data_intent():
    assert detect_chain_data_intent("show me recent ETH transfers")
    assert detect_chain_data_intent("who deployed tokens on Base yesterday")
    assert detect_chain_data_intent("whale swap on gnosis")
    assert not detect_chain_data_intent("is this token a honeypot")


# ── Canned intent SQL ─────────────────────────────────────────────────


def test_build_intent_sql_transfers():
    sql = build_intent_sql("recent ETH transfers", chain="ethereum", date=None, limit=5)
    assert "FROM chain_events" in sql
    assert "chain = 'ethereum'" in sql
    assert sql.strip().lower().startswith("select")


def test_build_intent_sql_dex():
    sql = build_intent_sql("whale swaps on base", chain="base", date=None, limit=10)
    assert "FROM chain_dex" in sql


def test_build_intent_sql_deploy():
    sql = build_intent_sql(
        "who deployed tokens on Base yesterday", chain="base", date=None, limit=5
    )
    assert "FROM chain_events" in sql
    assert "from_address = '0x0000000000000000000000000000000000000000'" in sql


def test_build_intent_sql_is_read_only():
    sql = build_intent_sql("recent transfers", chain="gnosis", date=None, limit=3)
    validate_read_only_sql(sql)  # must pass the shared guard


# ── run_chain_query over local parquet ────────────────────────────────


def test_run_chain_query_local_transfers(tmp_path):
    files = _write_chain_parquet(tmp_path)
    result = run_chain_query(
        "SELECT chain, tx_hash, from_address, value FROM chain_events "
        "ORDER BY block_timestamp DESC",
        files_override=files,
    )
    assert result["source"] == "local"
    assert result["row_count"] == 3
    assert result["rows"][0][1] == "0xtransfer3"  # newest block_timestamp (03:00) first


def test_run_chain_query_local_dex(tmp_path):
    files = _write_chain_parquet(tmp_path)
    result = run_chain_query("SELECT type, dex, whale FROM chain_dex", files_override=files)
    assert result["row_count"] == 1
    assert result["rows"][0][0] == "swap"
    assert result["rows"][0][1] == "uniswap_v3"


def test_run_chain_query_local_deploy_events(tmp_path):
    files = _write_chain_parquet(tmp_path)
    # Only row 1 has from_address = zero address (token creation).
    result = run_chain_query(
        "SELECT tx_hash FROM chain_events "
        "WHERE from_address = '0x0000000000000000000000000000000000000000'",
        files_override=files,
    )
    assert result["row_count"] == 1
    assert result["rows"][0][0] == "0xtransfer1"


def test_run_chain_query_glob_override(tmp_path):
    _write_chain_parquet(tmp_path)
    result = run_chain_query(
        "SELECT count(*) AS n FROM chain_events",
        glob_override=str(tmp_path / "gnosis_2026-08-03.parquet"),
    )
    assert result["row_count"] == 1
    assert result["rows"][0][0] == 3


def test_run_chain_query_rejects_write(tmp_path):
    files = _write_chain_parquet(tmp_path)
    with pytest.raises(ValueError, match="Only read-only"):
        run_chain_query("INSERT INTO chain_events VALUES (1)", files_override=files)


def test_run_chain_query_requires_chain_for_b2(tmp_path):
    # Without files/glob overrides and without a chain, B2 path must fail
    # with a clear error instead of attempting an empty prefix download.
    with pytest.raises(ValueError, match="chain is required"):
        run_chain_query("SELECT * FROM chain_events")

"""Fixture tests for the DexScreener promotion collector (no network, no ClickHouse)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("promotions", ROOT / "src/rugbrain/promotions.py")
promotions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promotions)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
TOP_FIXTURE = [
    {"chainId": "solana", "tokenAddress": "TokWithBoost", "url": "https://dexscreener.com/solana/a",
     "totalAmount": 500, "description": "sword"},
    {"chainId": "ethereum", "tokenAddress": "0xNoAmount", "url": "https://dexscreener.com/ethereum/b"},
]
LATEST_FIXTURE = [
    {"chainId": "solana", "tokenAddress": "TokLatest", "url": "https://dexscreener.com/solana/c",
     "totalAmount": 30, "amount": 30},
    {"chainId": "base", "tokenAddress": "0xOnlyTotal", "url": "https://dexscreener.com/base/d",
     "totalAmount": 120},
]
PROFILE_FIXTURE = [
    {"chainId": "solana", "tokenAddress": "TokProfile", "url": "https://dexscreener.com/solana/e",
     "cto": False},
    {"chainId": "solana", "tokenAddress": "TokCto", "url": "https://dexscreener.com/solana/f",
     "cto": True},
]


def test_parse_top_fixture_uses_total_amount():
    rows = promotions.parse_payload(TOP_FIXTURE, "boost", "dexscreener:token-boosts/top", NOW)
    by_token = {row["token_address"]: row for row in rows}
    assert by_token["TokWithBoost"]["amount_usd"] == 500.0
    assert by_token["TokWithBoost"]["chain"] == "solana"
    assert by_token["TokWithBoost"]["kind"] == "boost"
    assert by_token["TokWithBoost"]["paid"] == 1
    assert by_token["TokWithBoost"]["first_seen"] == NOW


def test_parse_latest_fixture_prefers_amount_and_falls_back_to_total():
    rows = promotions.parse_payload(LATEST_FIXTURE, "boost", "dexscreener:token-boosts/latest", NOW)
    by_token = {row["token_address"]: row for row in rows}
    assert by_token["TokLatest"]["amount_usd"] == 30.0
    assert by_token["0xOnlyTotal"]["amount_usd"] == 120.0


def test_missing_amount_is_none_not_zero():
    rows = promotions.parse_payload(TOP_FIXTURE, "boost", "src", NOW)
    assert {r["token_address"]: r["amount_usd"] for r in rows}["0xNoAmount"] is None
    profiles = promotions.parse_payload(PROFILE_FIXTURE, "profile", "src", NOW)
    assert all(row["amount_usd"] is None for row in profiles)


def test_profile_cto_kind_tagged():
    rows = promotions.parse_payload(PROFILE_FIXTURE, "profile", "src", NOW)
    kinds = {r["token_address"]: r["kind"] for r in rows}
    assert kinds["TokCto"] == "cto"
    assert kinds["TokProfile"] == "profile"


def test_dedupe_on_rerun_keeps_one_row_and_earliest_first_seen():
    first = promotions.parse_payload(TOP_FIXTURE, "boost", "src", NOW)
    later = promotions.parse_payload(TOP_FIXTURE, "boost", "src",
                                     datetime(2026, 10, 25, 13, 0, tzinfo=UTC))
    merged = promotions.dedupe_rows(first + later)
    assert len(merged) == len({(r["chain"], r["token_address"], r["kind"]) for r in merged})
    row = next(r for r in merged if r["token_address"] == "TokWithBoost")
    assert row["last_seen"].hour == 13
    assert row["first_seen"] == NOW


def test_paid_only_true_for_boost_only_token():
    rows = promotions.parse_payload(TOP_FIXTURE, "boost", "src", NOW)
    flags = promotions.paid_only_map(rows)
    assert flags[("solana", "TokWithBoost")] is True


def test_paid_only_false_when_token_also_has_profile():
    rows = promotions.parse_payload(TOP_FIXTURE, "boost", "src", NOW)
    rows += promotions.parse_payload(
        [{"chainId": "solana", "tokenAddress": "TokWithBoost", "cto": False}], "profile", "src", NOW)
    assert promotions.paid_only_map(rows)[("solana", "TokWithBoost")] is False

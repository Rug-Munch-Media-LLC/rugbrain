"""Per-chain discovery adapters: fixtures, dedupe, degradation, paid passthrough."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rugbrain.trending_fusion import discovery, geckoterminal, indexed_pairs
from rugbrain.trending_fusion.fuse import fuse, load_weights

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def _run(coro):
    return asyncio.run(coro)


def _source(name, rows, fail=False):
    async def _fetch(chain):
        if fail:
            raise RuntimeError(f"{name} net down")
        return rows

    return (name, _fetch)


def test_geckoterminal_parses_recorded_fixture():
    rows = geckoterminal.rows_from_payload(_fixture("geckoterminal_eth_trending.json"), "ethereum")
    assert rows, "recorded eth trending_pools fixture must produce candidates"
    row = rows[0]
    assert set(row) >= {"source", "chain", "token_address", "symbol", "signals", "paid"}
    assert row["source"] == "geckoterminal" and row["chain"] == "ethereum"
    assert row["token_address"].startswith("0x") and row["paid"] is False
    assert row["signals"]["volume_24h"] > 0 and row["signals"]["liquidity"] > 0
    assert 0.0 <= row["signals"]["buy_pressure"] <= 1.0
    assert 0.0 < row["signals"]["age"] <= 1.0


def test_geckoterminal_refuses_unknown_network_slug():
    assert geckoterminal.resolve_network("dogechain") is None
    with pytest.raises(geckoterminal.UnknownNetwork):
        _run(geckoterminal.geckoterminal_rows("dogechain"))


def test_indexed_pairs_parses_recorded_fixture():
    rows = indexed_pairs.rows_from_payload(_fixture("rmi_new_pairs_ethereum.json"), "ethereum")
    assert rows, "recorded new-pairs fixture must produce candidates"
    assert rows[0]["source"] == "rmi_indexed_pairs" and rows[0]["chain"] == "ethereum"
    assert rows[0]["token_address"].startswith("0x")
    assert 0.0 < rows[0]["signals"]["age"] <= 1.0


def test_indexed_pairs_skips_addressless_aggregates():
    payload = {"items": [
        {"symbol": "DACITY", "address": "", "token_address": "", "chain": "multi", "volume_24h": 3559482.0},
        {"symbol": "REAL", "token_address": "0xabc", "chain": "ethereum", "volume_usd": 10.0},
    ]}
    rows = indexed_pairs.rows_from_payload(payload, "ethereum")
    assert [r["token_address"] for r in rows] == ["0xabc"]


def test_dedupe_collapses_duplicates_keeping_max_signal():
    rows = [
        {"source": "geckoterminal", "chain": "base", "token_address": "0xaa", "symbol": "A",
         "signals": {"volume_24h": 5.0}, "paid": False},
        {"source": "geckoterminal", "chain": "base", "token_address": "0xaa", "symbol": "A",
         "signals": {"volume_24h": 9.0}, "paid": False},
    ]
    merged = discovery.dedupe_rows(rows)
    assert len(merged) == 1 and merged[0]["signals"]["volume_24h"] == 9.0


def test_one_source_down_degrades_and_names_it():
    rows = [{"source": "geckoterminal", "chain": "arbitrum", "token_address": "0xbb", "symbol": "B",
             "signals": {"volume_24h": 100.0}, "paid": False}]
    result = _run(discovery.gather_rows("arbitrum", sources=(
        _source("geckoterminal", rows),
        _source("rmi_indexed_pairs", [], fail=True),
    )))
    assert len(result["rows"]) == 1 and result["sources_ok"] == ["geckoterminal"]
    assert "rmi_indexed_pairs" in result["missing"] and "RuntimeError" in result["missing"]["rmi_indexed_pairs"]


def test_all_sources_down_refuses_instead_of_empty_ranked():
    result = _run(discovery.gather_rows("polygon", sources=(
        _source("geckoterminal", [], fail=True),
        _source("rmi_indexed_pairs", [], fail=True),
    )))
    assert result["rows"] == [] and result["sources_ok"] == []
    assert set(result["missing"]) == {"geckoterminal", "rmi_indexed_pairs"}
    assert fuse(result["rows"], weights=load_weights(), missing_sources=result["missing"])["ranked"] == []


def test_paid_passthrough_survives_fusion():
    rows = [
        {"source": "geckoterminal", "chain": "bsc", "token_address": "0xcc", "symbol": "C",
         "signals": {"volume_24h": 500.0}, "paid": False},
        {"source": "dexscreener", "chain": "bsc", "token_address": "0xdd", "symbol": "D",
         "signals": {"promotion_paid": 500.0}, "paid": True,
         "paid_kind": "dexscreener_boost", "paid_amount": 500.0},
    ]
    out = fuse(rows, weights=load_weights(), limit=10)
    assert [r["token_address"] for r in out["ranked"]] == ["0xcc"]
    assert out["paid_only_excluded"][0]["token_address"] == "0xdd"
    organic = next(r for r in out["ranked"] if r["token_address"] == "0xcc")
    assert organic["disclosed_promotions"] == []


def test_order_is_deterministic_across_calls():
    payload = _fixture("geckoterminal_eth_trending.json")
    first = geckoterminal.rows_from_payload(payload, "ethereum")
    second = geckoterminal.rows_from_payload(json.loads(json.dumps(payload)), "ethereum")
    assert [r["token_address"] for r in first] == [r["token_address"] for r in second]


def test_no_second_scorer_in_adapters():
    for module in (geckoterminal, indexed_pairs, discovery):
        source = Path(module.__file__).read_text()
        assert "load_weights" not in source, f"{module.__name__} must not load a second weight file"
        assert "trending_weights" not in source

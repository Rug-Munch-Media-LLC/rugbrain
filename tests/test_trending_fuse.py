"""Tests for trending fusion: dedupe, paid-only exclusion, weights, determinism."""

from __future__ import annotations

import json
import random

import pytest

from rugbrain.trending_fusion.fuse import fuse, load_weights


def _row(source, address, signal, value, paid=False, symbol="TKN"):
    row = {"source": source, "chain": "solana", "token_address": address, "symbol": symbol,
           "signals": {signal: value}}
    if paid:
        row.update({"paid": True, "paid_kind": f"{source}_paid", "paid_amount": value})
    return row


def test_dedupe_merges_evidence_by_chain_and_address():
    rows = [
        _row("dexscreener", "AddrA", "volume_24h", 1_000_000),
        _row("dexscreener", "AddrA", "liquidity", 500_000),
        _row("scanner", "AddrA", "scanner_score", 0.9),
        _row("dexscreener", "AddrB", "volume_24h", 10_000),
    ]
    result = fuse(rows, limit=10)
    assert [t["token_address"] for t in result["ranked"]] == ["AddrA", "AddrB"]
    evidence = result["ranked"][0]["evidence"]
    assert {(e["source"], e["signal"]) for e in evidence} == {
        ("dexscreener", "volume_24h"), ("dexscreener", "liquidity"), ("scanner", "scanner_score")
    }


def test_paid_only_token_cannot_rank():
    rows = [
        _row("dexscreener", "Organic", "volume_24h", 2_000_000),
        _row("dexscreener", "Boosted", "promotion_paid", 5000, paid=True),
        _row("gmgn", "Boosted", "promotion_paid", 700, paid=True),
    ]
    result = fuse(rows, limit=10)
    assert [t["token_address"] for t in result["ranked"]] == ["Organic"]
    excluded = result["paid_only_excluded"]
    assert [t["token_address"] for t in excluded] == ["Boosted"]
    assert excluded[0]["paid_only"] is True
    assert all(p["paid"] is True for p in excluded[0]["disclosed_promotions"])
    assert {p["source"] for p in excluded[0]["disclosed_promotions"]} == {"dexscreener", "gmgn"}
    # paid presence must not raise a token's score above an organic-only token
    assert all(t["score"] == 0.0 for t in excluded)


def test_every_paid_evidence_row_is_labelled():
    rows = [_row("dextools", "Promo", "promotion_paid", 1, paid=True)]
    result = fuse(rows, limit=5)
    assert result["paid_only_excluded"][0]["evidence"][0]["paid"] is True


def test_weight_file_validates(tmp_path):
    load_weights()  # shipped file is valid
    base = json.loads((__import__("rugbrain.trending_fusion.fuse", fromlist=["config_path"]).config_path()).read_text())

    bad_paid = json.loads(json.dumps(base))
    bad_paid["signals"]["promotion_paid"]["weight"] = 0.1
    path = tmp_path / "bad_paid.json"
    path.write_text(json.dumps(bad_paid))
    with pytest.raises(ValueError, match="negative"):
        load_weights(path)

    no_reason = json.loads(json.dumps(base))
    no_reason["signals"]["volume_24h"]["rationale"] = ""
    path2 = tmp_path / "no_reason.json"
    path2.write_text(json.dumps(no_reason))
    with pytest.raises(ValueError, match="rationale"):
        load_weights(path2)


def test_order_is_deterministic_under_shuffle():
    rows = [_row("dexscreener", a, "volume_24h", v) for a, v in
            [("A", 5.0), ("B", 4.0), ("C", 3.0), ("D", 2.0)]]
    first = fuse(rows, limit=10)["ranked"]
    shuffled = rows * 1
    random.shuffle(shuffled)
    second = fuse(shuffled, limit=10)["ranked"]
    assert [t["token_address"] for t in first] == [t["token_address"] for t in second]
    assert [t["score"] for t in first] == [t["score"] for t in second]

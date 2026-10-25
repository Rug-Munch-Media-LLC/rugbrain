"""One trending engine: chain registry, frozen envelope, absorbed bot signals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rugbrain.trending_fusion import api as trend_api
from rugbrain.trending_fusion.api import ENGINE, rugbrain_trending_fused
from rugbrain.trending_fusion.chains import CHAINS, resolve_chain
from rugbrain.trending_fusion.fuse import fuse, load_weights
from rugbrain.trending_fusion.router import router

REQUIRED_CHAINS = {"solana", "ethereum", "bsc", "base", "arbitrum", "polygon"}


def _row(addr, source="dexscreener", chain="solana", **signals):
    return {
        "source": source,
        "chain": chain,
        "token_address": addr,
        "symbol": addr[:4].upper(),
        "signals": signals,
    }


def _fuse(rows, **kwargs):
    return fuse(rows, weights=load_weights(), limit=kwargs.pop("limit", 10), **kwargs)


def _canned(ranked=None, paid_only=None):
    async def _inner(chain, limit=10):
        return {
            "chain": chain,
            "ranked": ranked
            if ranked is not None
            else [
                {
                    "rank": 1,
                    "chain": chain,
                    "token_address": "So111",
                    "symbol": "AAA",
                    "score": 0.5,
                    "evidence": [
                        {
                            "source": "dexscreener",
                            "signal": "volume_24h",
                            "value": 6500000.0,
                            "paid": False,
                        },
                        {
                            "source": "dexscreener",
                            "signal": "promotion_paid",
                            "value": 500.0,
                            "paid": True,
                        },
                    ],
                    "paid": True,
                    "paid_only": False,
                    "disclosed_promotions": [
                        {
                            "source": "dexscreener",
                            "kind": "dexscreener_boost",
                            "amount_usd": 500.0,
                            "paid": True,
                        }
                    ],
                }
            ],
            "paid_only_excluded": paid_only or [],
            "missing_sources": {"gmgn": "not wired", "discovery_scope": "dexscreener sample"},
        }

    return _inner


def _config_row(name):
    rows = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "chains.json").read_text(encoding="utf-8")
    )
    return {row["name"]: row for row in rows["chains"]}[name]


def test_registry_covers_required_chains():
    assert set(CHAINS) >= REQUIRED_CHAINS
    for spec in CHAINS.values():
        assert spec.native_token and spec.candidate_source
        assert spec.data_source == "dexscreener"
        # quote_asset is config-only: absent stays UNKNOWN, never guessed.
        assert spec.quote_asset == _config_row(spec.name).get("quote_asset")


def test_alias_resolution():
    assert resolve_chain("SOL").name == "solana"
    assert resolve_chain("eth").name == "ethereum"
    assert resolve_chain("bnb").name == "bsc"
    assert resolve_chain("matic").name == "polygon"
    assert resolve_chain("arb").name == "arbitrum"
    assert resolve_chain(" dogechain ") is None


def test_unknown_chain_refused_with_supported_names():
    result = asyncio.run(rugbrain_trending_fused("dogechain"))
    assert result["status"] == "unavailable"
    assert result["source"] == "chain_registry"
    assert "supported=" in result["reason"] and "ethereum" in result["reason"]
    assert "ranked" not in result


def test_paid_only_cannot_rank():
    out = _fuse([_row("PaidOnly1", promotion_paid=500.0)])
    assert out["ranked"] == []
    assert [p["token_address"] for p in out["paid_only_excluded"]] == ["PaidOnly1"]


def test_envelope_marks_paid_and_discloses_amount(monkeypatch):
    monkeypatch.setattr(trend_api, "trending_fused", _canned())
    result = asyncio.run(rugbrain_trending_fused("solana"))
    item = result["ranked"][0]
    assert item["paid"] is True
    assert item["disclosures"] == ["dexscreener_boost$500"]
    paid_evidence = [e for e in item["evidence"] if e["paid"]]
    assert paid_evidence and paid_evidence[0]["signal"] == "promotion_paid"


def test_missing_source_named_not_zero_weighted():
    rows = [_row("A" * 32, volume_24h=1000.0), _row("B" * 32, volume_24h=10.0)]
    plain = _fuse(rows)
    named = _fuse(rows, missing_sources={"gmgn": "not wired"})
    assert named["missing_sources"] == {"gmgn": "not wired"}
    assert named["ranked"][0]["score"] == plain["ranked"][0]["score"] > 0


def test_momentum_rewards_negative_movers():
    rows = [
        _row("DumpToken1", volume_24h=1000.0, momentum_24h=45.0),
        _row("FlatToken1", volume_24h=1000.0, momentum_24h=2.0),
    ]
    out = _fuse(rows)
    assert out["ranked"][0]["token_address"] == "DumpToken1"  # noqa: S105


def test_risk_penalty_lowers_score():
    rows = [_row("RiskToken1", volume_24h=5000.0, liquidity=90000.0)]
    clean = _fuse(list(rows))["ranked"][0]["score"]
    penalised = _fuse([*rows, _row("RiskToken1", source="scanner", risk_penalty=0.9)])["ranked"][0][
        "score"
    ]
    assert penalised < clean


def test_refuses_instead_of_empty_ranked(monkeypatch):
    monkeypatch.setattr(trend_api, "trending_fused", _canned(ranked=[]))
    result = asyncio.run(rugbrain_trending_fused("base"))
    assert result["status"] == "unavailable" and result["source"] == "dexscreener"
    assert result["reason"].startswith("no_rankable_candidates:")


def test_envelope_has_engine_and_timestamp(monkeypatch):
    monkeypatch.setattr(trend_api, "trending_fused", _canned())
    result = asyncio.run(rugbrain_trending_fused("solana", limit=1))
    assert result["engine"] == ENGINE == "rugbrain.trending_fusion/v1"
    assert result["generated_at"].endswith("Z") and len(result["generated_at"]) == 20
    assert result["missing_sources"] == ["gmgn"]
    assert result["stale"] is False


def test_route_reachable_in_process(monkeypatch):
    monkeypatch.setattr(trend_api, "trending_fused", _canned())
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/v1/trending?chain=eth&limit=1")
    assert response.status_code == 200
    body = response.json()
    assert body["chain"] == "ethereum" and body["engine"] == ENGINE
    assert body["ranked"][0]["evidence"][0]["source"] == "dexscreener"


def test_corpus_module_has_no_market_scoring():
    from rugbrain import corpus_trending

    assert "NOT market trending" in (corpus_trending.__doc__ or "")
    assert hasattr(corpus_trending, "score_corpus_trending")
    assert not hasattr(corpus_trending, "score_trending")
    assert not hasattr(corpus_trending, "trending_report")


def test_deterministic_order():
    rows = [_row(f"Token{index}" * 4, volume_24h=float(100 * index)) for index in range(1, 8)]
    first = _fuse(list(rows))["ranked"]
    shuffled = rows[3:] + rows[:3]  # different input order, same population
    second = _fuse(shuffled)["ranked"]
    assert [r["token_address"] for r in first] == [r["token_address"] for r in second]
    assert [r["score"] for r in first] == [r["score"] for r in second]

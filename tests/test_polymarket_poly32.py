"""poly-e2e: shared ref URL, 6 h freshness, MCP->gamma fallback, gate verdict."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain import polymarket_ref, polymarket_source
from rugbrain.polymarket_source import StaleMarketError, citation_line, market_odds

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
GAMMA_ROW = {
    "slug": "will-bitcoin-hit-100k-in-2026",
    "question": "Will Bitcoin hit $100k in 2026?",
    "outcomePrices": '["0.62", "0.38"]',
    "volume": "4821930.55",
    "endDate": "2026-12-31T00:00:00Z",
    "updatedAt": (NOW - timedelta(minutes=20)).isoformat(),
}


def _boom(*_args, **_kwargs):
    raise ConnectionError("mcp down")


def test_ref_url_shape_is_single_source(monkeypatch):
    monkeypatch.delenv(polymarket_ref.REF_ENV, raising=False)
    assert polymarket_ref.ref_url("abc") == "https://polymarket.com/market/abc?ref=rugmunch"
    monkeypatch.setenv(polymarket_ref.REF_ENV, "crm")
    assert polymarket_ref.ref_url("abc").endswith("/market/abc?ref=crm")
    with pytest.raises(ValueError):
        polymarket_ref.ref_url("  ")


def test_stale_market_over_six_hours_is_refused():
    stale = dict(GAMMA_ROW, updatedAt=(NOW - timedelta(hours=7)).isoformat())
    with pytest.raises(StaleMarketError):
        market_odds("bitcoin", mcp=_boom, gamma=lambda _q: stale, now=NOW)


def test_fresh_market_mcp_unavailable_falls_back_to_gamma():
    got = market_odds("bitcoin", mcp=_boom, gamma=lambda _q: GAMMA_ROW, now=NOW)
    assert got["path"] == "gamma"
    assert got["implied_pct"] == 62.0
    assert got["volume_usd"] == pytest.approx(4821930.55)
    assert got["resolution_date"] == "2026-12-31T00:00:00Z"
    assert got["url"] == polymarket_ref.ref_url(GAMMA_ROW["slug"])
    assert got["freshness"]["age_seconds"] == 1200


def test_mcp_path_wins_when_available():
    mcp = lambda tool, _args: (
        {"slug": "m", "question": "M?", "midpoint": 0.41,
         "volume": 10.0, "endDate": "2027-01-01", "fetched_at": NOW.isoformat()}
        if tool == "polymarket_market_snapshot" else {}
    )
    got = market_odds("m", mcp=mcp, gamma=lambda _q: pytest.fail("gamma used"), now=NOW)
    assert got["path"] == "mcp" and got["implied_pct"] == 41.0


def test_pipeline_gate_accepts_correct_citation_and_rejects_missing_ref():
    line = citation_line(market_odds("bitcoin", mcp=_boom, gamma=lambda _q: GAMMA_ROW, now=NOW))
    assert polymarket_ref.citation_ok(line, GAMMA_ROW["slug"]) is True
    assert polymarket_ref.citation_ok("BTC 62% on Polymarket. https://polymarket.com/market/x") is False
    assert polymarket_ref.citation_ok("see https://polymarket.com/market/x?ref=other") is False

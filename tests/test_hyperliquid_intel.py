"""Offline tests for the Hyperliquid intel analysis layer (fixtures, no network)."""

from __future__ import annotations

from rugbrain.hyperliquid_intel import (
    extract_liquidations,
    largest_positions,
    recent_entries,
    render_unit,
    summarise_cascade,
    summarise_window,
)

FILLS = [
    {"coin": "ETH", "px": "2363.6", "sz": "1.3037", "side": "A", "time": 1000,
     "tid": 1, "hash": "0xa", "dir": "Close Long",
     "liquidation": {"liquidatedUser": "0xvictim", "markPx": "2364.6", "method": "market"}},
    {"coin": "ETH", "px": "2364.0", "sz": "40.0", "side": "B", "time": 1100,
     "tid": 2, "hash": "0xb", "dir": "Open Long"},
    {"coin": "ETH", "px": "2365.0", "sz": "1.0", "side": "A", "time": 5000,
     "tid": 3, "hash": "0xc", "dir": "Close Long",
     "liquidation": {"liquidatedUser": "0xvictim", "markPx": "2365.0", "method": "market"}},
]


def test_extract_uses_only_window_and_flags_victim_side():
    liqs = extract_liquidations(FILLS, 900, 2000, reporter="0xvictim")
    assert len(liqs) == 1
    row = liqs[0]
    assert row["wallet"] == "0xvictim"
    assert row["forced_side"] == "A"          # victim's own fill: sold
    assert row["notional_usd"] == round(2363.6 * 1.3037, 2)
    assert row["mark_slip_bps"] < 0           # executed below mark


def test_counterparty_reporter_flips_side():
    liqs = extract_liquidations(FILLS, 900, 2000, reporter="0xmaker")
    assert liqs[0]["forced_side"] == "B"      # we read the taker, victim was bought back


def test_cascade_burst_needs_two_events():
    liqs = extract_liquidations(FILLS, 900, 2000, reporter="0xvictim")
    assert summarise_cascade(liqs)["burst"] is None
    two = extract_liquidations(FILLS, 900, 2000, reporter="0xvictim") * 2
    two[1] = {**two[0], "time": 1100, "tid": 9}
    burst = summarise_cascade(two)["burst"]
    assert burst["count"] == 2 and burst["dominant_forced_side"] == "A"


def test_window_stats_from_rows():
    rows = [{"kind": "trade", "coin": "ETH", "side": "B", "px": 2000.0, "sz": 2.0,
             "notional_usd": 4000.0, "time": 1000, "ts_ingest": 1040, "users": ["0xa", "0xb"]},
            {"kind": "mark", "coin": "ETH", "oraclePx": "2000", "markPx": "2000"}]
    stats = summarise_window(rows)
    assert stats["window"]["trades"] == 1
    assert stats["per_coin"]["ETH"]["buy_notional_usd"] == 4000.0
    assert stats["lag_ms"]["p50"] == 40


def test_positions_and_entries():
    states = {"0xwhale": {
        "marginSummary": {"accountValue": "1000000"},
        "assetPositions": [{"position": {
            "coin": "BTC", "szi": "-10", "leverage": {"type": "cross", "value": 20},
            "entryPx": "80000", "positionValue": "800000", "unrealizedPnl": "50000",
            "returnOnEquity": "0.05", "liquidationPx": "90000"}}]}}
    pos = largest_positions(states)[0]
    assert pos["side"] == "short" and pos["notional_usd"] == 800000.0
    assert pos["notional_over_equity"] == 0.8
    entries = recent_entries([{"user": "0xwhale", "dir": "Open Long", "coin": "ETH",
                               "px": "2000", "sz": "300", "time": 2000, "fee": "-1"}], 1000)
    assert entries[0]["notional_usd"] == 600000.0 and entries[0]["side"] == "long"


def test_render_unit_requires_numbers():
    assert render_unit("liq_cascade", notional=0, coin="ETH", minutes=1, count=1,
                       side="A", wallets=1) is None
    unit = render_unit("trader_underwater", short_wallet="0xabc", loss=1200.0,
                       coin="BTC", notional=5000.0, side="long", entry=80000.0,
                       roe=-1.5, liq_px=70000.0)
    assert "$1,200" in unit["text"] and "80000" not in unit["text"]

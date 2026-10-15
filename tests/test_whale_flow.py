"""Tests for the whale-flow signal projection.

Rows are payload-shaped, taken from the field list the wallet corpora document
(``address``, ``label``, ``name``, ``entity_type``, ``content``, ``metadata``).
The point of these tests is the CLASSIFICATION RULE and the risk boundary: an
unclassified address must read ``other``, never ``whale``, and a corpus with no
tainted addresses must read ``flow_risk == 0.0``.
"""

from __future__ import annotations

from rugbrain.whale_flow import classify_address, whale_flow_summary

EX = "0x28c6c06298d514db089934071355e5743bf21d60"
MIX = "0x1111111111111111111111111111111111111111"
EXP = "0x2222222222222222222222222222222222222222"
WHALE = "0x3333333333333333333333333333333333333333"
OTHER = "0x4444444444444444444444444444444444444444"


def _row(address: str, **extra: object) -> dict[str, object]:
    return {"address": address, **extra}


# ── classification ──────────────────────────────────────────────────────


def test_exchange_by_name_hint() -> None:
    assert classify_address(_row(EX, name="Binance: Hot Wallet")) == "exchange"


def test_exchange_by_entity_type() -> None:
    assert classify_address(_row(EX, entity_type="exchange")) == "exchange"


def test_mixer_by_label() -> None:
    assert classify_address(_row(MIX, label="Tornado Cash")) == "mixer"


def test_exploiter_by_entity_type() -> None:
    assert classify_address(_row(EXP, entity_type="exploiter")) == "exploiter"


def test_exploiter_by_phishing_label() -> None:
    assert classify_address(_row(EXP, label="Fake_Phishing6518")) == "exploiter"


def test_whale_by_name_hint() -> None:
    assert classify_address(_row(WHALE, name="Jump Trading")) == "whale"


def test_unclassified_is_other_not_whale() -> None:
    # The boundary: "never classified" is not "whale".
    assert classify_address(_row(OTHER, label="unknown")) == "other"
    assert classify_address(_row(OTHER)) == "other"


def test_mixer_beats_exchange_when_both_present() -> None:
    # A stronger laundering signal wins over a weaker one.
    row = _row(MIX, name="Binance", label="Tornado Cash")
    assert classify_address(row) == "mixer"


def test_exploiter_beats_mixer() -> None:
    row = _row(EXP, entity_type="exploiter", label="Tornado Cash")
    assert classify_address(row) == "exploiter"


def test_reads_nested_metadata() -> None:
    row = _row(EX, metadata={"name": "Coinbase 10", "entity_type": "exchange"})
    assert classify_address(row) == "exchange"


# ── summary ─────────────────────────────────────────────────────────────


def test_summary_counts_each_bucket() -> None:
    rows = [
        _row(EX, name="Binance"),
        _row(MIX, label="Tornado Cash"),
        _row(EXP, entity_type="exploiter"),
        _row(WHALE, name="Jump Trading"),
        _row(OTHER, label="unknown"),
    ]
    s = whale_flow_summary(rows)
    assert s["counts"] == {"exchange": 1, "mixer": 1, "exploiter": 1, "whale": 1, "other": 1}
    assert s["total"] == 5


def test_flow_risk_is_tainted_share() -> None:
    # 2 tainted (mixer + exploiter) of 4 classified = 0.5
    rows = [
        _row(MIX, label="Tornado Cash"),
        _row(EXP, entity_type="exploiter"),
        _row(EX, name="Binance"),
        _row(WHALE, name="Jump Trading"),
    ]
    s = whale_flow_summary(rows)
    assert s["flow_risk"] == 0.5
    assert s["risk_level"] == "critical"


def test_clean_corpus_reads_zero() -> None:
    rows = [_row(EX, name="Binance"), _row(WHALE, name="Jump Trading")]
    s = whale_flow_summary(rows)
    assert s["flow_risk"] == 0.0
    assert s["risk_level"] == "low"


def test_empty_corpus_reads_zero_not_error() -> None:
    s = whale_flow_summary([])
    assert s["total"] == 0
    assert s["flow_risk"] == 0.0
    assert s["risk_level"] == "low"
    assert s["flagged"] == []


def test_flagged_lists_tainted_addresses_deduped() -> None:
    rows = [
        _row(MIX, label="Tornado Cash"),
        _row(MIX, label="Tornado Cash"),  # duplicate address
        _row(EXP, entity_type="exploiter"),
    ]
    s = whale_flow_summary(rows)
    addrs = [f["address"] for f in s["flagged"]]
    assert addrs == [MIX, EXP]
    assert s["flagged"][0]["bucket"] == "mixer"


def test_risk_level_thresholds() -> None:
    # 1 tainted of 10 = 0.1 -> medium
    rows = [_row(EXP, entity_type="exploiter")] + [
        _row(f"0x{i:040x}", name="Binance") for i in range(9)
    ]
    s = whale_flow_summary(rows)
    assert s["flow_risk"] == 0.1
    assert s["risk_level"] == "medium"

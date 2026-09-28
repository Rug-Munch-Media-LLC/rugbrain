"""Tests for the scythe-radar projection, against the REAL archived CSV.

``tests/fixtures/scythe_dead_token_archive.csv`` is the live file from
``scytheradar/scythe-radar-datasets`` fetched 2026-09-28 (CC0-1.0, 19 rows).
The point of these tests is the free-text parsing: three numeric facts in that
archive exist ONLY inside the bilingual ``victims_note`` prose, and dropping
them is the difference between an event store and a text collection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rugbrain.rug_events.scythe import build_event, build_events, parse_csv
from rugbrain.rug_events.scythe_text import parse_holders, parse_money, parse_peak_tvl

FIXTURE = Path(__file__).parent / "fixtures" / "scythe_dead_token_archive.csv"
_ROWS = parse_csv(FIXTURE.read_text(encoding="utf-8"))


# ── the free-text numeric facts ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("链上持有人 103798", 103798),  # on-chain holders: 103798
        ("链上持有人 18", 18),
        ("约 30 万持有人", 300_000),  # "about 300k holders" — 万 = 10^4
    ],
)
def test_parse_holders_reads_the_chinese_prose(note: str, expected: int) -> None:
    count, _exact = parse_holders(note)
    assert count == expected


def test_parse_holders_marks_approximate_figures() -> None:
    """An approximate figure must never be presented as a measured one."""
    assert parse_holders("约 30 万持有人")[1] is False
    assert parse_holders("链上持有人 103798")[1] is True
    assert parse_holders("") == (None, False)


def test_parse_money_applies_the_wan_suffix() -> None:
    """$2626 万 = 26.26M — the archive's one documented cash-out figure."""
    assert parse_money("单次砸盘套现 $2626 万") == pytest.approx(26_260_000)


def test_parse_peak_tvl_is_not_a_loss() -> None:
    """Peak TVL and a cash-out share a ``$…万`` shape; they must not merge."""
    assert parse_peak_tvl("峰值 TVL $4590 万") == pytest.approx(45_900_000)
    assert parse_peak_tvl("单次砸盘套现 $2626 万") is None


def test_only_a_real_cashout_is_recorded_as_loss() -> None:
    """A peak-TVL note must not populate loss_usd.

    Both figures are ``$…万``; recording the peak as a loss would overstate a
    documented loss by the size of the token's entire TVL.
    """
    events = build_events(FIXTURE.read_text(encoding="utf-8"))
    ark = next(e for e in events if e["token"].startswith("ARK"))
    assert ark["loss_usd"] == pytest.approx(26_260_000)
    lax = next(e for e in events if e["token"].startswith("LAX"))
    assert "loss_usd" not in lax
    assert lax["peak_tvl_usd"] == pytest.approx(45_900_000)


# ── whole-row projection ────────────────────────────────────────────────


def test_archive_parses_to_nineteen_events() -> None:
    assert len(_ROWS) == 19
    assert len(build_events(FIXTURE.read_text(encoding="utf-8"))) == 19


def test_holder_counts_survive_the_projection() -> None:
    events = build_events(FIXTURE.read_text(encoding="utf-8"))
    with_holders = [e for e in events if "holders_at_rug" in e]
    assert len(with_holders) >= 15  # 16 of 19 rows document a holder count
    assert max(e["holders_at_rug"] for e in with_holders) >= 100_000


def test_mechanism_and_early_warning_are_carried() -> None:
    events = build_events(FIXTURE.read_text(encoding="utf-8"))
    ark = next(e for e in events if e["token"].startswith("ARK"))
    assert ark["rug_mechanism"] == (
        "Treasury/reserve narrative + unlimited mint dumps + extra-long lockup to trap liquidity"
    )
    assert ark["rug_outcome"] == "rugged_relaunch"
    assert ark["early_warning"]


def test_rug_ts_is_never_invented() -> None:
    """The archive publishes a narrative timeline, not a date.

    Fabricating ``rug_ts`` here would be the single easiest way to corrupt the
    collection, so it stays empty and the timeline is kept under its own name.
    """
    for event in build_events(FIXTURE.read_text(encoding="utf-8")):
        assert event["rug_ts"] == ""
    ark = build_events(FIXTURE.read_text(encoding="utf-8"))[0]
    assert ark["timeline"] == "砸盘 → 重启"


def test_unattributed_deployer_is_flagged_not_assumed_clean() -> None:
    event = build_events(FIXTURE.read_text(encoding="utf-8"))[0]
    assert event["deployer"] == ""
    assert event["deployer_attributed"] is False
    assert event["deployer_prior_rugs"] == 0


def test_row_without_address_is_rejected() -> None:
    assert build_event({"contract_address": "", "chain": "BSC"}) is None
    assert build_event({}) is None

"""Unit tests for rugbrain.scam_fields — funds-lost and severity parsing.

Split out of `scam_patterns.py` on 2026-09-29 (size ratchet). These two
functions are the only place a malformed loss figure or an unknown severity
label can corrupt a ranked pattern list, so both are pinned by value here.

The ticker case is the load-bearing one: on 2026-09-28 the single-letter
suffix regex matched the leading B of a TOKEN TICKER, so '15114 BUSD' parsed as
$15,114,000,000,000 and became the top-ranked pattern in the whole collection.
"""

from __future__ import annotations

import pytest

from rugbrain.scam_fields import max_severity, parse_funds_lost


class TestParseFundsLostShapes:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, 0.0),
            (1_500_000, 1_500_000.0),
            (1.5, 1.5),
            ("", 0.0),
            ("   ", 0.0),
            ("abc", 0.0),
            ("0", 0.0),
        ],
    )
    def test_scalar_and_empty_shapes(self, raw: object, expected: float) -> None:
        assert parse_funds_lost(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("$1,500,000", 1_500_000.0),
            ("1.5M", 1_500_000.0),
            ("1.2k", 1_200.0),
            ("7 million", 7_000_000.0),
            ("2.5 billion", 2_500_000_000.0),
            ("9 THOUSAND", 9_000.0),
        ],
    )
    def test_suffix_scales(self, raw: str, expected: float) -> None:
        assert parse_funds_lost(raw) == pytest.approx(expected)


class TestParseFundsLostTickerGuard:
    """A single-letter suffix must not match a token ticker's leading letter."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("15114 BUSD", 15114.0),
            ("1,000,000 BUSD", 1_000_000.0),
            ("~ 50 BNB", 50.0),
            ("3.3 BNB", 3.3),
        ],
    )
    def test_ticker_is_not_a_magnitude(self, raw: str, expected: float) -> None:
        assert parse_funds_lost(raw) == pytest.approx(expected)

    def test_ticker_does_not_outrank_a_real_billion(self) -> None:
        # The exact shape of the incident: a 15114-token loss parsed as 15tn
        # became the top-ranked pattern in the collection.
        assert parse_funds_lost("15114 BUSD") < parse_funds_lost("2.5 billion")

    def test_spelled_out_magnitude_still_works(self) -> None:
        # The guard is only on the single-letter forms; adding it to the
        # spelled-out group broke '2.5 billion' -> 2.
        assert parse_funds_lost("2.5 billion") == pytest.approx(2_500_000_000.0)


class TestMaxSeverity:
    def test_empty_list_is_info(self) -> None:
        assert max_severity([]) == "info"

    def test_picks_the_highest(self) -> None:
        assert max_severity(["low", "critical", "medium"]) == "critical"

    def test_case_and_whitespace_insensitive(self) -> None:
        assert max_severity([" HIGH ", "low"]) == "high"

    def test_unknown_label_ranks_as_info(self) -> None:
        assert max_severity(["nonsense", "low"]) == "low"

    def test_all_unknown_returns_the_first_unknown_label(self) -> None:
        # The rank table only knows the five canonical severities, so an
        # unrecognised label scores 0. Since best_rank starts at -1, the first
        # unrecognised label (rank 0) still beats the initial value and is
        # returned verbatim. That is the pre-split behaviour, preserved: an
        # unranked label reaching the caller is visible rather than silently
        # rewritten to "info", which would hide a vocabulary drift.
        assert max_severity(["nonsense", "gibberish"]) == "nonsense"

    def test_empty_list_is_the_only_path_to_a_bare_info(self) -> None:
        assert max_severity([]) == "info"

    def test_never_downgrades_a_real_label(self) -> None:
        assert max_severity(["critical", "nonsense"]) == "critical"

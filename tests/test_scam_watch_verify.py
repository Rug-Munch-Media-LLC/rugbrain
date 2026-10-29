"""The verification gate is the refusal path: one source can never publish."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.scam_watch.models import EvidenceItem, Signal
from rugbrain.scam_watch.verify import assess

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
SIGNALS = (
    Signal(code="contract_address", weight=0.35, detail="address in title", address="0xabc"),
    Signal(code="missing_disclosure", weight=0.25, detail="no disclosure"),
)


def row(domain: str, kind: str = "platform", age_hours: int = 1) -> EvidenceItem:
    return EvidenceItem(
        kind=kind,  # type: ignore[arg-type]
        url=f"https://{domain}/evidence",
        source=domain,
        captured_at=NOW - timedelta(hours=age_hours),
        claim="observed",
        confidence=0.9,
    )


def test_two_domains_with_onchain_evidence_pass():
    verdict = assess(SIGNALS, [row("rugmunch.io", "onchain"), row("solscan.io")], NOW)
    assert verdict.publishable is True
    assert verdict.failures == ()
    assert verdict.independent_sources == 2


def test_single_source_is_refused_even_when_onchain():
    verdict = assess(SIGNALS, [row("rugmunch.io", "onchain")], NOW)
    assert verdict.publishable is False
    assert "fewer_than_two_independent_sources" in verdict.failures


def test_two_urls_from_one_publisher_count_once():
    verdict = assess(
        SIGNALS,
        [row("rugmunch.io", "onchain"), row("rugmunch.io", "platform", age_hours=2)],
        NOW,
    )
    assert verdict.independent_sources == 1
    assert "fewer_than_two_independent_sources" in verdict.failures


def test_missing_onchain_row_is_refused():
    verdict = assess(SIGNALS, [row("rugmunch.io"), row("solscan.io")], NOW)
    assert "no_onchain_evidence" in verdict.failures


def test_stale_evidence_is_not_counted():
    verdict = assess(
        SIGNALS,
        [row("rugmunch.io", "onchain", age_hours=900), row("solscan.io")],
        NOW,
    )
    assert "stale_evidence:1" in verdict.failures
    assert verdict.independent_sources == 1


def test_undated_future_row_is_not_counted():
    verdict = assess(SIGNALS, [row("rugmunch.io", "onchain"), row("solscan.io", age_hours=-5)], NOW)
    assert "stale_evidence:1" in verdict.failures


def test_marginal_score_fails_the_confidence_floor():
    weak = (Signal(code="urgency_language", weight=0.45, detail="act now"),)
    verdict = assess(weak, [row("rugmunch.io", "onchain"), row("solscan.io")], NOW)
    assert verdict.publishable is False
    assert "confidence_below_floor" in verdict.failures


@pytest.mark.parametrize("count", [0, 1])
def test_zero_or_one_signal_is_refused(count):
    verdict = assess(list(SIGNALS[:count]), [row("rugmunch.io", "onchain"), row("x.io")], NOW)
    assert "insufficient_signals" in verdict.failures

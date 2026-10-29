"""Verdict regression tests for the address scam check.

The defect these lock down: a blue-chip contract scored ``high`` because a
semantic search over the scam corpora returned high-severity rows that were
not about the queried address. Verdicts must be address-scoped and severity
graded, and a verified blue chip must not be flagged by absence of evidence —
while a labelled blue chip still can be.
"""

from __future__ import annotations

import pytest

from rugbrain.blue_chips import VERIFIED_BLUE_CHIPS, lookup_blue_chip
from rugbrain.scam_verdict import decide_verdict

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
#: Label rows recorded in wallet_v2_8b (Etherscan Fake_Phishing labels).
LABELLED_SCAMS = (
    "0x58a166f9a6ff996e2349eea90d42cc198529a037",
    "0x79949b5353006b12cc0d739190d492c577bed080",
)


def _hit(address: str, severity: str) -> tuple[str, dict[str, object]]:
    return (
        "wallet_v2_8b",
        {
            "metadata": {"address": address},
            "severity": severity,
            "content": f"Wallet {address} labeled: Fake_Phishing",
        },
    )


@pytest.mark.parametrize("address", sorted(VERIFIED_BLUE_CHIPS))
def test_blue_chips_without_labels_are_low(address: str) -> None:
    verdict = decide_verdict(address, [])
    assert verdict["scam_risk"] == "low"
    assert verdict["verdict_reason"].startswith("verified_blue_chip:")


def test_blue_chip_registry_lookup_is_case_insensitive() -> None:
    assert lookup_blue_chip(USDC) is lookup_blue_chip(USDC.lower())


def test_blue_chip_with_high_label_is_still_high() -> None:
    verdict = decide_verdict(USDC, [_hit(USDC, "high")])
    assert verdict["scam_risk"] == "high"
    assert verdict["verdict_reason"] == "address_label_high"


def test_unscoped_high_severity_rows_do_not_move_the_verdict() -> None:
    noise = [_hit(LABELLED_SCAMS[0], "critical"), _hit(LABELLED_SCAMS[1], "high")]
    verdict = decide_verdict(USDC, noise)
    assert verdict["scam_risk"] == "low"


@pytest.mark.parametrize("address", LABELLED_SCAMS)
def test_labelled_scam_addresses_are_high(address: str) -> None:
    verdict = decide_verdict(address, [_hit(address, "high")])
    assert verdict["scam_risk"] == "high"
    assert verdict["confidence"] == 90


def test_unlabelled_unknown_address_is_unknown_not_low() -> None:
    verdict = decide_verdict("0x0000000000000000000000000000000000000001", [])
    assert verdict["scam_risk"] == "unknown"
    assert verdict["verdict_reason"] == "no_address_scoped_evidence"


def test_medium_and_low_labels_grade_downwards() -> None:
    addr = "0x0000000000000000000000000000000000000002"
    assert decide_verdict(addr, [_hit(addr, "medium")])["scam_risk"] == "medium"
    assert decide_verdict(addr, [_hit(addr, "low")])["scam_risk"] == "low"

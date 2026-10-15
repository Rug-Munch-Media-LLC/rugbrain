"""Tests for ``rugbrain.wallet_profile`` — the real wallet-profile verdict.

The defect this closes: ``rugbrain_wallet_profile`` returned
``{"findings": [], "risk_score": 0.5}`` for every address. These tests pin the
replacement against REAL payload shapes read from the live collections
(``wallet_v2_8b``, ``wallet_profiles_8b``, ``known_scams_8b``), so the verdict
rule is exercised on the data it will actually see.
"""

from __future__ import annotations

import pytest

from rugbrain.wallet_profile import profile_wallet
from rugbrain.wallet_profile_rules import (
    LABEL_COLLECTIONS,
    build_profile,
    classify_hit,
)

# ── Real payload shapes (copied from live Qdrant scroll, 2026-10-15) ──────

MALICIOUS_HIT = {
    "content": "Wallet 0x58a166f9a6ff996e2349eea90d42cc198529a037 on ethereum "
    "labeled: Fake_Phishing6518 (entity_type=malicious)",
    "metadata": {
        "address": "0x58a166f9a6ff996e2349eea90d42cc198529a037",
        "chain": "ethereum",
        "label": "Fake_Phishing6518",
        "entity_type": "malicious",
        "source": "etherscan_malicious",
    },
}

BENIGN_HIT = {
    "content": "Etherscan labeled address: 0xea5e288b4f9d7e66da162e2af0db371783f62454 "
    "(Synthetix: Deployer 4) on ethereum. Category: defi.",
    "metadata": {
        "address": "0xea5e288b4f9d7e66da162e2af0db371783f62454",
        "label": "defi",
        "name": "Synthetix: Deployer 4",
        "chain": "ethereum",
        "source": "etherscan_combined",
    },
}

UNCLASSIFIED_HIT = {
    "content": "Etherscan labeled address: 0xabc on ethereum.",
    "metadata": {
        "address": "0xabc",
        "label": "unknown_thing",
        "chain": "ethereum",
        "source": "etherscan_combined",
    },
}


class TestClassifyHit:
    """The label -> risk-level rule, on real payloads."""

    def test_malicious_entity_type_is_critical(self) -> None:
        assert classify_hit(MALICIOUS_HIT, "wallet_v2_8b") == "critical"

    def test_phishing_label_is_critical(self) -> None:
        hit = {"metadata": {"label": "Fake_Phishing123"}, "content": ""}
        assert classify_hit(hit, "wallet_v2_8b") == "critical"

    def test_benign_protocol_label_is_low(self) -> None:
        assert classify_hit(BENIGN_HIT, "wallet_profiles_8b") == "low"

    def test_known_scams_hit_is_high(self) -> None:
        hit = {"metadata": {"label": "some_domain"}, "content": "listed domain"}
        assert classify_hit(hit, "known_scams_8b") == "high"

    def test_unclassified_label_is_medium(self) -> None:
        assert classify_hit(UNCLASSIFIED_HIT, "wallet_profiles_8b") == "medium"

    def test_malicious_beats_benign(self) -> None:
        # A label carrying both a benign word and a malicious marker is malicious.
        hit = {"metadata": {"label": "defi_phishing_scam"}, "content": ""}
        assert classify_hit(hit, "wallet_profiles_8b") == "critical"


class TestBuildProfile:
    """The verdict assembly — pure, no I/O."""

    def test_no_hits_is_unknown_not_clean(self) -> None:
        p = build_profile("0xdead", [])
        assert p["found"] is False
        assert p["risk_level"] == "unknown"
        assert p["risk_score"] == 0.0
        assert p["findings"] == []
        assert "UNSEEN" in p["note"]

    def test_malicious_hit_is_critical(self) -> None:
        p = build_profile("0x58a1", [("wallet_v2_8b", MALICIOUS_HIT)])
        assert p["found"] is True
        assert p["risk_level"] == "critical"
        assert p["risk_score"] == 0.95
        assert len(p["findings"]) == 1
        assert p["findings"][0]["label"] == "Fake_Phishing6518"
        assert p["findings"][0]["collection"] == "wallet_v2_8b"

    def test_benign_hit_is_low(self) -> None:
        p = build_profile("0xea5e", [("wallet_profiles_8b", BENIGN_HIT)])
        assert p["risk_level"] == "low"
        assert p["risk_score"] == 0.15

    def test_worst_level_wins_across_hits(self) -> None:
        p = build_profile(
            "0xmulti",
            [("wallet_profiles_8b", BENIGN_HIT), ("wallet_v2_8b", MALICIOUS_HIT)],
        )
        assert p["risk_level"] == "critical"
        assert len(p["findings"]) == 2

    def test_labels_are_deduped_and_sorted(self) -> None:
        p = build_profile(
            "0xdup",
            [("wallet_profiles_8b", BENIGN_HIT), ("wallet_profiles_8b", BENIGN_HIT)],
        )
        assert p["labels"] == ["defi"]

    def test_collections_searched_is_reported(self) -> None:
        p = build_profile("0xdead", [])
        assert p["collections_searched"] == list(LABEL_COLLECTIONS)


class TestProfileWallet:
    """The async entrypoint — shape and guard behaviour."""

    @pytest.mark.asyncio
    async def test_empty_address_errors(self) -> None:
        assert (await profile_wallet(""))["error"] == "address is required"

    @pytest.mark.asyncio
    async def test_evm_address_shape_recorded(self) -> None:
        p = await profile_wallet("0x58a166f9a6ff996e2349eea90d42cc198529a037")
        assert p["address_shape"] == "evm"
        assert p["max_hops"] == 2

    @pytest.mark.asyncio
    async def test_non_evm_address_shape_recorded(self) -> None:
        p = await profile_wallet("So11111111111111111111111111111111111111112")
        assert p["address_shape"] == "non-evm"

    @pytest.mark.asyncio
    async def test_never_returns_the_old_stub(self) -> None:
        """The regression this module exists to prevent.

        The old path returned ``{"findings": [], "risk_score": 0.5}`` for every
        address. A real profile must never carry that exact shape.
        """
        p = await profile_wallet("0x0000000000000000000000000000000000000000")
        assert not (p.get("findings") == [] and p.get("risk_score") == 0.5)
        assert "risk_level" in p
        assert "found" in p

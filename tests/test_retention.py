"""Tests for the knowledge-retention loop (promotion, decay, signatures).

The property under test throughout is ANTI-POISONING: an attacker who can
inflate the observation stream must not be able to promote a false signature.
The interesting cases are therefore the attacks, not the happy path — one
subject replayed, one producer reporting everything, low-confidence noise, a
scanner reporting itself — plus the human review escape hatch.
"""

from __future__ import annotations

import pytest

from rugbrain.retention.decay import (
    DecayPolicy,
    apply_decay,
    refutation_weight,
    refuting_observations,
)
from rugbrain.retention.promotion import (
    PromotionPolicy,
    apply_promotion,
    eligible_observations,
    evaluate_promotion,
)
from rugbrain.retention.signatures import Observation, Signature

# ── fixtures ─────────────────────────────────────────────────────────


def _obs(subject: str, producer: str, verdict: str = "scam", conf: float = 0.9) -> Observation:
    return Observation(
        subject=subject,
        chain="ethereum",
        verdict=verdict,
        producer=producer,
        confidence=conf,
    )


def _sig() -> Signature:
    return Signature(
        signature_id="sig-1",
        name="honeypot: cannot sell",
        features={"is_honeypot": True},
    )


# ── signature model ──────────────────────────────────────────────────


class TestObservation:
    def test_subject_is_required(self) -> None:
        with pytest.raises(ValueError, match="subject"):
            Observation(subject="", chain="ethereum", verdict="scam", producer="a")

    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_confidence_is_bounded(self, bad: float) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _obs("0xabc", "a", conf=bad)

    def test_verdict_normalisation(self) -> None:
        assert _obs("0xabc", "a", verdict="HONEYPOT").is_adverse
        assert _obs("0xabc", "a", verdict="Safe").is_clean
        assert not _obs("0xabc", "a", verdict="weird").is_adverse

    def test_producer_normalisation(self) -> None:
        assert _obs("0xabc", "  GoPlus  ").normalised_producer == "goplus"
        assert Observation("s", "e", "rug", "").normalised_producer == "unknown"

    def test_builtin_verdict_vocabulary(self) -> None:
        assert _obs("0xabc", "a", verdict="rug").is_adverse
        assert _obs("0xabc", "a", verdict="safe").is_clean


class TestSignature:
    def test_no_corroboration_means_no_sources(self) -> None:
        assert _sig().independent_sources() == 0

    def test_self_corroborating_producers_collapse_to_one(self) -> None:
        sig = _sig()
        sig.corroboration = {"scanner": "0xa", "unknown": "0xb"}
        assert sig.independent_sources() == 1

    def test_independent_producers_count_separately(self) -> None:
        sig = _sig()
        sig.corroboration = {"goplus": "0xa", "rugcheck": "0xb", "scanner": "0xc"}
        assert sig.independent_sources() == 2


# ── promotion: anti-poisoning ─────────────────────────────────────────


class TestPromotionPolicy:
    def test_default_requires_three_producers(self) -> None:
        assert PromotionPolicy().min_independent_producers == 3

    def test_low_confidence_observations_are_ineligible(self) -> None:
        obs = [_obs(f"0x{i}", "goplus", conf=0.1) for i in range(5)]
        assert eligible_observations(obs) == []

    def test_clean_verdicts_never_corroborate(self) -> None:
        assert eligible_observations([_obs("0xa", "goplus", verdict="safe")]) == []


class TestPromotion:
    def test_three_independent_producers_two_subjects_promotes(self) -> None:
        obs = [
            _obs("0xa", "goplus"),
            _obs("0xb", "rugcheck"),
            _obs("0xc", "chainabuse"),
        ]
        d = evaluate_promotion(obs)
        assert d.promoted, d.reason
        assert d.producers == 3
        assert d.subjects == 3

    def test_replaying_one_subject_never_promotes(self) -> None:
        # ATTACK: 50 observations of the same honeypot, one producer.
        obs = [_obs("0xaaa", "goplus") for _ in range(50)]
        d = evaluate_promotion(obs)
        assert not d.promoted
        assert any("subject" in r for r in d.rejections)

    def test_one_producer_reporting_many_subjects_never_promotes(self) -> None:
        # ATTACK: a single compromised collector floods distinct subjects.
        obs = [_obs(f"0x{i}", "scanner") for i in range(50)]
        d = evaluate_promotion(obs)
        assert not d.promoted
        assert any("producer" in r for r in d.rejections)

    def test_scanner_cannot_corroborate_itself(self) -> None:
        obs = [_obs(f"0x{i}", "scanner") for i in range(10)]
        assert not evaluate_promotion(obs).promoted

    def test_two_producers_still_insufficient(self) -> None:
        obs = [_obs("0xa", "goplus"), _obs("0xb", "rugcheck")]
        d = evaluate_promotion(obs)
        assert not d.promoted
        assert d.producers == 2

    def test_no_evidence_is_rejected_with_a_reason(self) -> None:
        d = evaluate_promotion([])
        assert not d.promoted
        assert d.reason == "insufficient_evidence"

    def test_human_approval_promotes_without_machine_corroboration(self) -> None:
        # The review path must work during an active incident.
        obs = [_obs("0xaaa", "scanner", conf=0.95)]
        d = evaluate_promotion(obs, human_approved=True)
        assert d.promoted
        assert d.reason == "human_approved"

    def test_human_approval_does_not_promote_on_weak_evidence(self) -> None:
        obs = [_obs("0xaaa", "scanner", conf=0.5)]
        d = evaluate_promotion(obs, human_approved=True)
        assert not d.promoted
        assert any("human_approved" in r for r in d.rejections)

    def test_human_approval_with_no_adverse_evidence_does_not_promote(self) -> None:
        obs = [_obs("0xaaa", "scanner", verdict="safe", conf=0.99)]
        assert not evaluate_promotion(obs, human_approved=True).promoted

    def test_policy_can_be_tightened(self) -> None:
        obs = [_obs(f"0x{i}", f"p{i}") for i in range(3)]
        strict = PromotionPolicy(min_independent_producers=4)
        assert not evaluate_promotion(obs, strict).promoted

    def test_rejection_reasons_name_every_broken_rule(self) -> None:
        d = evaluate_promotion([_obs("0xa", "scanner")])
        assert len(d.rejections) == 2  # both subjects and producers short


class TestApplyPromotion:
    def test_approved_promotion_mutates_signature(self) -> None:
        sig = _sig()
        obs = [_obs("0xa", "goplus"), _obs("0xb", "rugcheck"), _obs("0xc", "chainabuse")]
        sig, d = apply_promotion(sig, obs, now=1000.0)
        assert d.promoted
        assert sig.promoted_at == 1000.0
        assert set(sig.corroboration) == {"goplus", "rugcheck", "chainabuse"}
        assert sig.confidence == 1.0

    def test_refused_promotion_leaves_signature_untouched(self) -> None:
        sig = _sig()
        sig, d = apply_promotion(sig, [_obs("0xa", "goplus")])
        assert not d.promoted
        assert sig.promoted_at == 0.0
        assert sig.corroboration == {}

    def test_ineligible_observations_do_not_enter_corroboration(self) -> None:
        sig = _sig()
        obs = [
            _obs("0xa", "goplus"),
            _obs("0xb", "rugcheck"),
            _obs("0xc", "chainabuse"),
            _obs("0xd", "noisy", conf=0.1),
        ]
        sig, _ = apply_promotion(sig, obs)
        assert "noisy" not in sig.corroboration


# ── decay ────────────────────────────────────────────────────────────


class TestDecay:
    def test_no_refutations_no_retirement(self) -> None:
        _, d = apply_decay(_sig(), [])
        assert not d.retired
        assert d.reason == "no_refutation"

    def test_one_refutation_below_default_threshold(self) -> None:
        sig, d = apply_decay(_sig(), [_obs("0xaaa", "goplus", verdict="safe")])
        assert not d.retired
        assert sig.refuted_by == ["0xaaa"]

    def test_two_refutations_retire(self) -> None:
        _, d = apply_decay(
            _sig(),
            [
                _obs("0xaaa", "goplus", verdict="safe"),
                _obs("0xbbb", "rugcheck", verdict="safe"),
            ],
        )
        assert d.retired
        assert d.reason == "refuted"

    def test_refutation_does_not_delete_history(self) -> None:
        # A signature that was real once stays on the record.
        sig = _sig()
        sig.promoted_at = 500.0
        sig, _ = apply_decay(sig, [_obs("0xaaa", "goplus", verdict="safe")])
        assert sig.promoted_at == 500.0
        assert sig.refuters["0xaaa"] == "goplus"

    def test_one_subject_refutes_once_however_many_producers(self) -> None:
        obs = [
            _obs("0xaaa", "goplus", verdict="safe"),
            _obs("0xaaa", "rugcheck", verdict="safe"),
        ]
        sig, d = apply_decay(_sig(), obs, DecayPolicy(max_refutations=2))
        assert not d.retired
        assert len(sig.refuters) == 1

    def test_replaying_one_clean_subject_never_retires(self) -> None:
        obs = [_obs("0xaaa", "goplus", verdict="safe") for _ in range(20)]
        _, d = apply_decay(_sig(), obs, DecayPolicy(max_refutations=2))
        assert not d.retired

    def test_self_corroborating_refutation_is_downweighted(self) -> None:
        # A lone scanner saying "safe" twice must not equal two real sources.
        obs = [
            _obs("0xaaa", "scanner", verdict="safe"),
            _obs("0xbbb", "scanner", verdict="safe"),
        ]
        _, d = apply_decay(_sig(), obs, DecayPolicy(max_refutations=2))
        assert d.refutation_score < 2.0

    def test_staleness_alone_never_retires(self) -> None:
        # Time is not evidence: an old signature is still unrefuted.
        sig = _sig()
        sig.promoted_at = 1.0
        sig, d = apply_decay(sig, [])
        assert not d.retired
        assert d.refutation_score == 0.0

    def test_decay_is_idempotent(self) -> None:
        # A replayed batch must not change the score or re-report refutations.
        obs = [_obs("0xaaa", "goplus", verdict="safe")]
        sig = _sig()
        sig, first = apply_decay(sig, obs)
        sig, second = apply_decay(sig, obs)
        assert first.newly_refuted == ("0xaaa",)
        assert second.newly_refuted == ()
        assert first.refutation_score == second.refutation_score == 1.0
        assert sig.refuted_by == ["0xaaa"]

    def test_two_subjects_from_one_producer_each_count_but_are_downweighted(self) -> None:
        obs = [
            _obs("0xaaa", "scanner", verdict="safe"),
            _obs("0xbbb", "scanner", verdict="safe"),
        ]
        sig, d = apply_decay(_sig(), obs, DecayPolicy(max_refutations=2))
        assert len(sig.refuters) == 2
        assert d.refutation_score == pytest.approx(1.0)

    def test_refutation_weight_by_producer(self) -> None:
        assert refutation_weight("goplus") == 1.0
        assert refutation_weight("scanner") < 1.0
        assert refutation_weight("") < 1.0

    def test_adverse_observations_are_not_refutations(self) -> None:
        assert refuting_observations([_obs("0xa", "goplus", verdict="scam")]) == []

    def test_zero_confidence_clean_report_is_ignored(self) -> None:
        obs = [_obs("0xa", "goplus", verdict="safe", conf=0.0)]
        assert refuting_observations(obs) == []

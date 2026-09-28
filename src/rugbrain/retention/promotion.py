"""
Promotion policy — the anti-poisoning gate.

**The threat.** An attacker who can get one token scanned once (or who controls
one telemetry producer) must not be able to teach the detector a false
signature. If promotion needed only *N adverse observations*, an attacker who
re-scans one honeypot N times, or who controls one producer reporting N
subjects, would promote a signature that then matches every future token.

**The rules, in order of precedence.**

1. A subject counts **once**, no matter how many times it is observed. Replay
   is not corroboration.
2. A producer counts **once**, no matter how many subjects it reports. A
   single compromised collector cannot manufacture a quorum.
3. The default producers (``scanner``/``unknown``) are **self-corroborating**:
   they collapse to a single source. They can never, alone, satisfy
   ``min_independent_producers``.
4. A single **high-confidence human** sign-off promotes immediately — humans
   are the review path, and waiting for machine corroboration would make the
   loop useless during an active incident.
5. Minimum observation confidence is enforced, so a stream of low-confidence
   noise cannot promote anything.

Everything here is a pure function. No I/O, no clock beyond ``now``, so the
policy is testable in isolation and cannot be bypassed by a caller who forgets
a step.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rugbrain.retention.signatures import Observation, Signature

# A promoted signature must rest on at least this many INDEPENDENT producers.
DEFAULT_MIN_PRODUCERS = 3

# ...covering at least this many distinct subjects.
DEFAULT_MIN_SUBJECTS = 2

# Observations below this confidence are recorded but cannot corroborate.
DEFAULT_MIN_OBSERVATION_CONFIDENCE = 0.6

# A human-approved promotion is trusted outright.
DEFAULT_HUMAN_MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class PromotionPolicy:
    """Tunable thresholds for promotion. Defaults are the anti-poisoning set."""

    min_independent_producers: int = DEFAULT_MIN_PRODUCERS
    min_distinct_subjects: int = DEFAULT_MIN_SUBJECTS
    min_observation_confidence: float = DEFAULT_MIN_OBSERVATION_CONFIDENCE

    def to_dict(self) -> dict[str, float | int]:
        return {
            "min_independent_producers": self.min_independent_producers,
            "min_distinct_subjects": self.min_distinct_subjects,
            "min_observation_confidence": self.min_observation_confidence,
        }


@dataclass(frozen=True)
class PromotionDecision:
    """Why a signature was (or was not) promoted — audit trail for the gate."""

    promoted: bool
    reason: str
    producers: int
    subjects: int
    rejections: tuple[str, ...] = ()


def eligible_observations(
    observations: list[Observation],
    policy: PromotionPolicy = PromotionPolicy(),
) -> list[Observation]:
    """Adverse observations that are confident enough to count as evidence."""
    return [
        o
        for o in observations
        if o.is_adverse and o.confidence >= policy.min_observation_confidence
    ]


def evaluate_promotion(
    observations: list[Observation],
    policy: PromotionPolicy = PromotionPolicy(),
    human_approved: bool = False,
) -> PromotionDecision:
    """Decide whether these observations may become a durable signature.

    Args:
        observations: every observation gathered for the candidate pattern.
        policy: thresholds; see :class:`PromotionPolicy`.
        human_approved: a human reviewer signed off on this pattern.

    Returns:
        A :class:`PromotionDecision` whose ``reason`` names the exact rule that
        decided the outcome — this string is what an auditor reads.
    """
    # The human path considers ALL adverse observations at human-grade
    # confidence, not merely the machine-eligible ones: a reviewer signing off
    # is a different question from "is this corroborated automatically".
    eligible = eligible_observations(observations, policy)
    human_eligible = [
        o
        for o in observations
        if o.is_adverse and o.confidence >= DEFAULT_HUMAN_MIN_CONFIDENCE
    ]

    if human_approved and human_eligible:
        h_subjects = {o.subject.strip().lower() for o in human_eligible}
        h_producers = {o.normalised_producer for o in human_eligible}
        return PromotionDecision(
            promoted=True,
            reason="human_approved",
            producers=len(h_producers),
            subjects=len(h_subjects),
        )

    subjects = {o.subject.strip().lower() for o in eligible}
    producers = {o.normalised_producer for o in eligible}
    rejections: list[str] = []

    if human_approved and not human_eligible:
        strongest = max(
            (o.confidence for o in observations if o.is_adverse),
            default=0.0,
        )
        rejections.append(
            f"human_approved but max adverse confidence {strongest:.2f} < "
            f"{DEFAULT_HUMAN_MIN_CONFIDENCE}"
        )

    if not eligible:
        rejections.append(
            f"no adverse observation with confidence >= {policy.min_observation_confidence}"
        )
        return PromotionDecision(False, "insufficient_evidence", 0, 0, tuple(rejections))

    if len(subjects) < policy.min_distinct_subjects:
        rejections.append(
            f"{len(subjects)} distinct subject(s) < {policy.min_distinct_subjects}"
        )
    if len(producers) < policy.min_independent_producers:
        rejections.append(
            f"{len(producers)} producer(s) < {policy.min_independent_producers}"
        )

    if rejections:
        return PromotionDecision(
            False, "not_corroborated", len(producers), len(subjects), tuple(rejections)
        )
    return PromotionDecision(
        True, "corroborated", len(producers), len(subjects), tuple(rejections)
    )


def apply_promotion(
    signature: Signature,
    observations: list[Observation],
    policy: PromotionPolicy = PromotionPolicy(),
    human_approved: bool = False,
    now: float | None = None,
) -> tuple[Signature, PromotionDecision]:
    """Apply an approved promotion to *signature*; return (signature, decision).

    A refused decision is returned unchanged, so the caller can persist the
    audit trail without mutating the signature.
    """
    decision = evaluate_promotion(observations, policy, human_approved)
    if not decision.promoted:
        return signature, decision

    for obs in eligible_observations(observations, policy):
        signature.corroboration.setdefault(obs.normalised_producer, obs.subject)
    signature.promoted_at = now if now is not None else time.time()
    signature.human_approved = signature.human_approved or human_approved
    independent = signature.independent_sources()
    signature.confidence = min(1.0, independent / max(1, policy.min_independent_producers))
    return signature, decision


__all__ = [
    "DEFAULT_HUMAN_MIN_CONFIDENCE",
    "DEFAULT_MIN_OBSERVATION_CONFIDENCE",
    "DEFAULT_MIN_PRODUCERS",
    "DEFAULT_MIN_SUBJECTS",
    "PromotionDecision",
    "PromotionPolicy",
    "apply_promotion",
    "eligible_observations",
    "evaluate_promotion",
]

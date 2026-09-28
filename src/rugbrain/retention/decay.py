"""
Decay policy — stop trusting a signature that has stopped being supported.

Promotion without decay is how a detection system slowly poisons itself: a
signature promoted during one incident keeps matching forever, and every false
match is a false positive the analyst has to chase.

**Rules.**

* A clean verdict on a subject that helped promote the signature counts as
  **one refutation**. Refutations are evidence, not deletions.
* Once ``refutations >= max_refutations``, the signature is **retired**. It
  stops matching but is *not* deleted — a scam signature that was real once is
  evidence, and the audit trail matters.
* Refutation from a self-corroborating producer is down-weighted, for the same
  reason promotion is: one collector cannot both build and destroy trust.
* Staleness alone never retires a signature. Time is not evidence; a quiet
  year is not proof of innocence.
"""

from __future__ import annotations

from dataclasses import dataclass

from rugbrain.retention.signatures import SELF_CORROBORATING_PRODUCERS, Observation, Signature

# Refutations tolerated before a promoted signature is retired.
DEFAULT_MAX_REFUTATIONS = 2

# A self-corroborating producer's refutation counts as this much.
SELF_CORROBORATION_REFUTATION_WEIGHT = 0.5


@dataclass(frozen=True)
class DecayPolicy:
    """Thresholds for retiring a signature."""

    max_refutations: float = DEFAULT_MAX_REFUTATIONS


@dataclass(frozen=True)
class DecayDecision:
    """Outcome of applying one batch of refuting observations."""

    retired: bool
    reason: str
    refutation_score: float
    newly_refuted: tuple[str, ...] = ()


def refutation_weight(producer: str) -> float:
    """How much one clean report from *producer* counts against a signature."""
    key = producer.strip().lower() or "unknown"
    if key in SELF_CORROBORATING_PRODUCERS:
        return SELF_CORROBORATION_REFUTATION_WEIGHT
    return 1.0


def refuting_observations(observations: list[Observation]) -> list[Observation]:
    """Clean observations that are worth counting against a signature."""
    return [o for o in observations if o.is_clean and o.confidence > 0.0]


def apply_decay(
    signature: Signature,
    observations: list[Observation],
    policy: DecayPolicy = DecayPolicy(),
) -> tuple[Signature, DecayDecision]:
    """Fold clean observations into *signature* and retire it past the limit.

    Returns the mutated signature plus the decision. Refuted subjects are
    recorded on the signature so a later audit can see *why* it was retired.
    """
    new_refutations: list[str] = []
    for obs in refuting_observations(observations):
        subject = obs.subject.strip().lower()
        if subject in signature.refuted_by:
            continue  # one subject refutes once, however many producers saw it
        signature.refuted_by.append(subject)
        signature.refuters[subject] = obs.normalised_producer
        new_refutations.append(subject)

    # Recomputed from the recorded (subject -> producer) map so a re-run is
    # idempotent: each refuting subject is weighted by ITS OWN producer.
    score = sum(refutation_weight(producer) for producer in signature.refuters.values())
    retired = len(signature.refuted_by) >= policy.max_refutations
    reason = "refuted" if retired else "below_threshold"
    if not signature.refuted_by:
        reason = "no_refutation"
    return signature, DecayDecision(retired, reason, score, tuple(new_refutations))


__all__ = [
    "DEFAULT_MAX_REFUTATIONS",
    "SELF_CORROBORATION_REFUTATION_WEIGHT",
    "DecayDecision",
    "DecayPolicy",
    "apply_decay",
    "refutation_weight",
    "refuting_observations",
]

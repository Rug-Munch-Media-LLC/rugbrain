"""Signature + observation models for the retention loop.

A **signature** is a durable crime pattern: "tokens with ``is_honeypot`` and
``top10_pct > 0.9`` from a deployer with a prior rug". A **signature hit** is
one independent observation of that pattern on one subject (a token address).

Promotion arithmetic lives in :mod:`rugbrain.retention.promotion`; this module
is only the vocabulary, kept small so the schema can be reasoned about in one
screen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Producers that cannot corroborate each other. Two reports from the same
# producer are ONE source of evidence, not two — otherwise a single compromised
# collector could manufacture a quorum on its own.
SELF_CORROBORATING_PRODUCERS = frozenset({"scanner", "unknown", ""})

# Verdicts that count as adverse (evidence *for* a signature).
ADVERSE_VERDICTS = frozenset({"scam", "honeypot", "rug", "critical", "exploit"})

# Verdicts that count as exonerating (evidence *against* one).
CLEAN_VERDICTS = frozenset({"safe", "clean", "legit", "verified"})


@dataclass(frozen=True)
class Observation:
    """One independent observation of a subject, from one named producer."""

    subject: str
    chain: str
    verdict: str
    producer: str
    confidence: float = 0.0
    evidence: str = ""
    features: dict[str, object] = field(default_factory=dict)
    observed_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("observation.subject is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def normalised_producer(self) -> str:
        return self.producer.strip().lower() or "unknown"

    @property
    def is_adverse(self) -> bool:
        return self.verdict.strip().lower() in ADVERSE_VERDICTS

    @property
    def is_clean(self) -> bool:
        return self.verdict.strip().lower() in CLEAN_VERDICTS

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.observed_at) / 86400.0) if self.observed_at else 0.0


@dataclass
class Signature:
    """A durable, promoted crime pattern with its corroboration record."""

    signature_id: str
    name: str
    description: str = ""
    features: dict[str, object] = field(default_factory=dict)
    # producer -> subject it was corroborated on (one subject per producer).
    corroboration: dict[str, str] = field(default_factory=dict)
    # Subjects later exonerated after promotion.
    refuted_by: list[str] = field(default_factory=list)
    # refuting subject -> the producer that refuted it, so the weighted
    # refutation score is recomputable and idempotent.
    refuters: dict[str, str] = field(default_factory=dict)
    promoted_at: float = 0.0
    human_approved: bool = False
    confidence: float = 0.0

    def independent_sources(self) -> int:
        """Distinct corroborating producers (self-corroborating ones collapse to 1)."""
        real = {
            p for p in self.corroboration if p not in SELF_CORROBORATING_PRODUCERS
        }
        return len(real) if real else (1 if self.corroboration else 0)


__all__ = [
    "ADVERSE_VERDICTS",
    "CLEAN_VERDICTS",
    "SELF_CORROBORATING_PRODUCERS",
    "Observation",
    "Signature",
]

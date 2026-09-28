"""Data model for the retrieval baseline ratchet — and the exit-code contract.

Split out of ``baseline_ratchet`` so the CONTRACT (what each exit code means)
is readable on its own, next to the types it describes. The policy that
decides a verdict lives in :mod:`rugbrain.eval.ratchet_verdict`; the
serialisation lives in :mod:`rugbrain.eval.ratchet_store`.

Why the exit codes must stay distinct
-------------------------------------
Collapsing any two of these into one non-zero code is how a broken monitor
stays green for a month:

    0  PASS                every measured metric is within delta of baseline
    1  REGRESSED           a measured metric fell more than delta below baseline
    2  COULD_NOT_MEASURE   no baseline, or the corpus could not be measured
    3  BASELINE_MISMATCH   the baseline on disk does not describe this corpus
    4  DIM_SPLIT           serving/store dimension disagreement invalidates recall

``1`` means "something got worse, go attribute it". ``2`` means "we do not
know" and must never be read as "fine". ``3`` means the baseline is stale and
its number is meaningless. ``4`` means the measurement itself is invalid.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# ── exit codes ───────────────────────────────────────────────────────
EXIT_PASS = 0
EXIT_REGRESSED = 1
EXIT_COULD_NOT_MEASURE = 2
EXIT_BASELINE_MISMATCH = 3
EXIT_DIM_SPLIT = 4

EXIT_MEANING: dict[int, str] = {
    EXIT_PASS: "pass",
    EXIT_REGRESSED: "regressed",
    EXIT_COULD_NOT_MEASURE: "could-not-measure",
    EXIT_BASELINE_MISMATCH: "baseline-hash-mismatch",
    EXIT_DIM_SPLIT: "dimension-split",
}

Layer = Literal["dense", "hybrid", "rerank"]
LAYERS: tuple[Layer, ...] = ("dense", "hybrid", "rerank")

#: The layer production actually serves. The gate is decided here.
PRODUCTION_LAYER: Layer = "hybrid"

BASELINE_SCHEMA = 1


@dataclass(frozen=True)
class LayerMetrics:
    """Quality on one retrieval layer at every measured cutoff."""

    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    measured: int = 0
    errors: int = 0

    @property
    def measurable(self) -> bool:
        """True when at least one query actually returned results."""
        return self.measured > 0 and self.errors < self.measured

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Measurement:
    """All layers for one run, plus the corpus fingerprint it measured."""

    corpus_hash: str
    case_count: int
    layers: dict[str, LayerMetrics] = field(default_factory=dict)
    dim_checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def layer(self) -> LayerMetrics:
        return self.layers.get(PRODUCTION_LAYER, LayerMetrics())

    def as_dict(self) -> dict[str, Any]:
        return {
            "corpus_hash": self.corpus_hash,
            "case_count": self.case_count,
            "layers": {k: v.as_dict() for k, v in self.layers.items()},
            "dim_checks": self.dim_checks,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Measurement:
        return cls(
            corpus_hash=raw.get("corpus_hash", ""),
            case_count=int(raw.get("case_count", 0)),
            layers={
                k: LayerMetrics(**{f: v[f] for f in LayerMetrics.__dataclass_fields__ if f in v})
                for k, v in (raw.get("layers") or {}).items()
            },
            dim_checks=list(raw.get("dim_checks") or []),
        )


@dataclass(frozen=True)
class Regression:
    """One metric that fell further than the allowed delta."""

    layer: str
    metric: str
    baseline: float
    current: float
    delta: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Verdict:
    """The gate's decision, fully attributed."""

    exit_code: int
    reason: str
    regressions: tuple[Regression, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "meaning": EXIT_MEANING[self.exit_code],
            "reason": self.reason,
            "regressions": [r.as_dict() for r in self.regressions],
        }


#: Metrics the ratchet watches, and how far each may fall before it counts.
#: Recall moves in visible steps as the corpus shifts; MRR and nDCG are noisier
#: at 43 cases, so they carry a looser delta. A tighter tolerance on a noisy
#: metric just produces alert fatigue and a baseline nobody trusts.
DEFAULT_TOLERANCE: dict[str, float] = {
    "recall_at_5": 0.02,
    "recall_at_10": 0.03,
    "mrr": 0.05,
    "ndcg_at_10": 0.05,
}


def tolerance_for(overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Merge caller tolerances over :data:`DEFAULT_TOLERANCE`."""
    return {**DEFAULT_TOLERANCE, **(overrides or {})}


def dumps(payload: dict[str, Any]) -> str:
    """Canonical, human-diffable JSON for a baseline file."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "BASELINE_SCHEMA",
    "DEFAULT_TOLERANCE",
    "EXIT_BASELINE_MISMATCH",
    "EXIT_COULD_NOT_MEASURE",
    "EXIT_DIM_SPLIT",
    "EXIT_MEANING",
    "EXIT_PASS",
    "EXIT_REGRESSED",
    "LAYERS",
    "PRODUCTION_LAYER",
    "Layer",
    "LayerMetrics",
    "Measurement",
    "Regression",
    "Verdict",
    "dumps",
    "tolerance_for",
]

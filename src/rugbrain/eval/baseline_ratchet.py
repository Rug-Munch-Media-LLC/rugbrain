"""Baseline ratchet for retrieval quality — with distinct exit codes.

Replaces the ``gate_min_recall=0.0`` monitor. A gate whose floor is 0.0 cannot
fail, so it is a log line, not a gate. This module gates against a *stored
baseline* and a *regression delta*, so a real drop in quality turns into a real
non-zero exit.

Exit-code contract
------------------
0  PASS             every measured metric is within delta of baseline
1  REGRESSED        a measured metric fell more than delta below baseline
2  COULD_NOT_MEASURE no baseline, or the corpus could not be measured at all
3  BASELINE_MISMATCH  the baseline on disk does not describe the current corpus
4  DIM_SPLIT          the dimension preflight found serving/store disagreement

The split matters operationally. ``1`` means "something got worse, look at the
attribution". ``2`` means "we do not know" and must never be read as "fine".
``3`` means the baseline is stale and the number is meaningless. ``4`` means
the measurement itself is invalid. Collapsing any two of these into a single
non-zero code is how a broken monitor stays green for a month.

Per-layer attribution
---------------------
Quality is measured on three layers independently, because "recall went down"
does not tell you where:

  dense   semantic-only recall. A regression here is an embedding/corpus change.
  hybrid  dense+sparse with RRF fusion — the PRODUCTION path. A dense-only
          number is not a production number.
  rerank  hybrid plus the rerank stage. RERANK_BACKEND=auto puts rerank in the
          request path and it reorders results, so measuring without it
          reports quality the user never sees.

The gate is decided on the PRODUCTION layer (hybrid) by default. The others are
reported so a hybrid regression can be attributed rather than merely detected.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
# The layer production actually serves. The gate is decided here.
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
    """All three layers for one run, plus the corpus fingerprint it measured."""

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


# Metrics the ratchet watches, and how far each may fall before it counts.
# Recall moves in visible steps as the corpus shifts; MRR and nDCG are noisier
# at 43 cases, so they carry a looser delta. A tighter tolerance on a noisy
# metric just produces alert fatigue and a baseline nobody trusts.
DEFAULT_TOLERANCE: dict[str, float] = {
    "recall_at_5": 0.02,
    "recall_at_10": 0.03,
    "mrr": 0.05,
    "ndcg_at_10": 0.05,
}


def load_baseline(path: Path | str) -> Measurement | None:
    """Load the stored baseline. ``None`` when absent or unreadable.

    An absent baseline is COULD_NOT_MEASURE, never PASS. There is no such
    thing as "no baseline therefore fine".
    """
    path = Path(path)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != BASELINE_SCHEMA:
        raise ValueError(
            f"baseline schema mismatch in {path}: expected {BASELINE_SCHEMA}, "
            f"got {raw.get('schema')!r}"
        )
    return Measurement.from_dict(raw.get("measurement") or {})


def write_baseline(path: Path | str, measurement: Measurement) -> None:
    """Record a measurement as the new baseline.

    Deliberately a separate, explicit action. Re-recording a baseline is how a
    red gate gets made green; it must never be a side effect of running one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BASELINE_SCHEMA,
        "measurement": measurement.as_dict(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def evaluate(
    current: Measurement,
    baseline: Measurement | None,
    layer: Layer = PRODUCTION_LAYER,
    tolerance: dict[str, float] | None = None,
) -> Verdict:
    """Compare a measurement to its baseline and produce a verdict.

    Order of checks matters and is itself part of the contract:

    1. dimension split first — if the serving and store dims disagree, every
       recall number below is meaningless, so a regression verdict would be a
       lie. Report the split, not the recall.
    2. baseline absent → could not measure. Never pass.
    3. baseline present but its corpus hash does not describe the current
       corpus → the baseline is stale and its numbers cannot be compared.
    4. only then, per-metric regression with attribution.
    """
    tol = {**DEFAULT_TOLERANCE, **(tolerance or {})}

    split = [c for c in current.dim_checks if not c.get("ok", True)]
    if split:
        names = ", ".join(c.get("collection", "?") for c in split)
        return Verdict(
            exit_code=EXIT_DIM_SPLIT,
            reason=f"dimension split on {names}; metrics below are not trustworthy",
        )

    if baseline is None:
        return Verdict(
            exit_code=EXIT_COULD_NOT_MEASURE,
            reason="no baseline recorded; run with --write-baseline after a human review",
        )

    if baseline.corpus_hash and current.corpus_hash != baseline.corpus_hash:
        return Verdict(
            exit_code=EXIT_BASELINE_MISMATCH,
            reason=(
                f"baseline was recorded against corpus {baseline.corpus_hash[:12]} "
                f"but the current corpus is {current.corpus_hash[:12]}; "
                "the stored numbers do not describe what was measured"
            ),
        )

    cur = current.layers.get(layer) or LayerMetrics()
    if not cur.measurable:
        return Verdict(
            exit_code=EXIT_COULD_NOT_MEASURE,
            reason=(
                f"layer {layer!r} produced no usable results "
                f"(measured={cur.measured} errors={cur.errors}); "
                "an unmeasured layer is not a passing layer"
            ),
        )

    base = baseline.layers.get(layer) or LayerMetrics()
    if not base.measurable:
        return Verdict(
            exit_code=EXIT_COULD_NOT_MEASURE,
            reason=f"stored baseline for layer {layer!r} is not measurable",
        )

    regressions: list[Regression] = []
    for metric, allowed in tol.items():
        base_val = getattr(base, metric, None)
        cur_val = getattr(cur, metric, None)
        if base_val is None or cur_val is None:
            continue
        drop = base_val - cur_val
        if drop > allowed:
            regressions.append(
                Regression(
                    layer=layer,
                    metric=metric,
                    baseline=round(base_val, 4),
                    current=round(cur_val, 4),
                    delta=round(drop, 4),
                )
            )

    if regressions:
        detail = ", ".join(
            f"{r.metric} {r.baseline}->{r.current} (-{r.delta})" for r in regressions
        )
        return Verdict(
            exit_code=EXIT_REGRESSED,
            reason=f"{layer} regression: {detail}",
            regressions=tuple(regressions),
        )

    return Verdict(exit_code=EXIT_PASS, reason=f"{layer} within tolerance of baseline")


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
    "Layer",
    "LayerMetrics",
    "Measurement",
    "PRODUCTION_LAYER",
    "Regression",
    "Verdict",
    "evaluate",
    "load_baseline",
    "write_baseline",
]

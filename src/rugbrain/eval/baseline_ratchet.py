"""Baseline ratchet for retrieval quality — with distinct exit codes.

Replaces the ``gate_min_recall=0.0`` monitor. A gate whose floor is 0.0 cannot
fail, so it is a log line, not a gate. This package gates against a *stored
baseline* and a *regression delta*, so a real drop in quality turns into a real
non-zero exit.

Public surface (unchanged — the names below are the import contract used by
``eval.ratchet``, the CLI, the smoke script and the tests)::

    from rugbrain.eval.baseline_ratchet import evaluate, load_baseline, ...

The implementation is split by responsibility, each module one job:

    ratchet_model    the exit-code contract and the measurement types
    ratchet_verdict  the check ORDER and the per-metric comparison policy
    ratchet_store    what a baseline file is and how it may be written

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

from rugbrain.eval.ratchet_model import (
    BASELINE_SCHEMA,
    DEFAULT_TOLERANCE,
    EXIT_BASELINE_MISMATCH,
    EXIT_COULD_NOT_MEASURE,
    EXIT_DIM_SPLIT,
    EXIT_MEANING,
    EXIT_PASS,
    EXIT_REGRESSED,
    LAYERS,
    PRODUCTION_LAYER,
    Layer,
    LayerMetrics,
    Measurement,
    Regression,
    Verdict,
)
from rugbrain.eval.ratchet_store import load_baseline, write_baseline
from rugbrain.eval.ratchet_verdict import evaluate

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
    "evaluate",
    "load_baseline",
    "write_baseline",
]

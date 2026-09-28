"""Verdict policy for the retrieval baseline ratchet — pure decision, no I/O.

Split out of ``baseline_ratchet`` so the ORDER of checks can be read without
wading through the serialisation code. The order is itself part of the
contract, and it is load-bearing: an earlier version checked the baseline
hash first, so a corpus that had also drifted in dimension reported a stale
baseline instead of the invalid measurement.

    1. dimension split first — if the serving and store dims disagree, every
       recall number below is meaningless, so a regression verdict would be a
       lie. Report the split, not the recall.
    2. baseline absent -> could not measure. Never pass. There is no such
       thing as "no baseline therefore fine".
    3. baseline present but its corpus hash does not describe the current
       corpus -> the baseline is stale and its numbers cannot be compared.
    4. only then, per-metric regression with attribution.

Note that steps 2 and 3 are both COULD_NOT_MEASURE/BASELINE_MISMATCH rather
than regressions, and that an unmeasurable layer is never a passing layer: a
layer where every query errored reports ``measured=0`` and must surface as
"we do not know", not as a zero that gets compared against the floor.
"""

from __future__ import annotations

from rugbrain.eval.ratchet_model import (
    EXIT_BASELINE_MISMATCH,
    EXIT_COULD_NOT_MEASURE,
    EXIT_DIM_SPLIT,
    EXIT_PASS,
    EXIT_REGRESSED,
    PRODUCTION_LAYER,
    Layer,
    LayerMetrics,
    Measurement,
    Regression,
    Verdict,
    tolerance_for,
)


def evaluate(
    current: Measurement,
    baseline: Measurement | None,
    layer: Layer = PRODUCTION_LAYER,
    tolerance: dict[str, float] | None = None,
) -> Verdict:
    """Compare a measurement to its baseline and produce a verdict."""
    tol = tolerance_for(tolerance)

    verdict = _check_dim_split(current)
    if verdict is not None:
        return verdict

    if baseline is None:
        return Verdict(
            exit_code=EXIT_COULD_NOT_MEASURE,
            reason="no baseline recorded; run with --write-baseline after a human review",
        )

    verdict = _check_corpus_hash(current, baseline)
    if verdict is not None:
        return verdict

    verdict = _check_layer_measurable(current, baseline, layer)
    if verdict is not None:
        return verdict

    regressions = _find_regressions(
        current.layers.get(layer) or LayerMetrics(), baseline, layer, tol
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


def _check_dim_split(current: Measurement) -> Verdict | None:
    """A serving/store dimension disagreement invalidates every metric below."""
    split = [c for c in current.dim_checks if not c.get("ok", True)]
    if not split:
        return None
    names = ", ".join(c.get("collection", "?") for c in split)
    return Verdict(
        exit_code=EXIT_DIM_SPLIT,
        reason=f"dimension split on {names}; metrics below are not trustworthy",
    )


def _check_corpus_hash(current: Measurement, baseline: Measurement) -> Verdict | None:
    """Refuse to compare numbers recorded against a different corpus."""
    if not baseline.corpus_hash or current.corpus_hash == baseline.corpus_hash:
        return None
    return Verdict(
        exit_code=EXIT_BASELINE_MISMATCH,
        reason=(
            f"baseline was recorded against corpus {baseline.corpus_hash[:12]} "
            f"but the current corpus is {current.corpus_hash[:12]}; "
            "the stored numbers do not describe what was measured"
        ),
    )


def _check_layer_measurable(
    current: Measurement, baseline: Measurement, layer: Layer
) -> Verdict | None:
    """Neither side may be compared while either one measured nothing."""
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
    return None


def _find_regressions(
    cur: LayerMetrics,
    baseline: Measurement,
    layer: Layer,
    tol: dict[str, float],
) -> list[Regression]:
    """Per-metric drops that exceed their own tolerance."""
    base = baseline.layers.get(layer) or LayerMetrics()
    out: list[Regression] = []
    for metric, allowed in tol.items():
        base_val = getattr(base, metric, None)
        cur_val = getattr(cur, metric, None)
        if base_val is None or cur_val is None:
            continue
        drop = base_val - cur_val
        if drop > allowed:
            out.append(
                Regression(
                    layer=layer,
                    metric=metric,
                    baseline=round(base_val, 4),
                    current=round(cur_val, 4),
                    delta=round(drop, 4),
                )
            )
    return out


__all__ = ["evaluate"]

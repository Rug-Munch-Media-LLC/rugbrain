"""Ranking metrics over per-rank relevance gains (chunk/content mode).

Sibling of :mod:`rugbrain.eval.rank_ids`: instead of matching retrieved ids
against a labeled relevant-id set, the caller supplies one gain per retrieved
rank (1.0 when a chunk contains a gold phrase, else 0.0 — or a graded gain).
Everything is already normalised, so these functions do no id matching.

The ideal DCG is computed from the best possible ordering of the actual gains,
so a ranking that puts every relevant chunk first scores 1.0.
"""

from __future__ import annotations

from collections.abc import Sequence

from rugbrain.eval.gain_discount import clamp_k, dcg_at_k

__all__ = ["mrr_from_gains", "ndcg_from_gains", "recall_from_gains"]


def ndcg_from_gains(gains: Sequence[float], k: int | None = None) -> float:
    """NDCG@k from per-rank relevance gains (binary 0/1 or graded).

    Returns 0.0 for an empty sequence, a zero cutoff, or when no rank carries
    positive gain.
    """
    if not gains:
        return 0.0
    k = clamp_k(k, len(gains))
    if k <= 0:
        return 0.0
    ideal = sorted((float(g) for g in gains), reverse=True)[:k]
    idcg = dcg_at_k(ideal, k)
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(gains, k) / idcg


def mrr_from_gains(gains: Sequence[float]) -> float:
    """Reciprocal rank of the first positive gain (rank is 1-indexed).

    Returns 0.0 when no rank carries positive gain, and 0.0 for an empty
    sequence. Mirrors ``rank_ids.mrr`` for the per-rank-gain mode.
    """
    for i, g in enumerate(gains):
        if g > 0.0:
            return 1.0 / (i + 1)
    return 0.0


def recall_from_gains(gains: Sequence[float], k: int) -> float:
    """Fraction of the top-*k* results that are relevant.

    Bounded by construction: the numerator is a subset of the denominator, so
    unlike the id-family recall — where an empty relevant set returns 1.0 — this
    can never exceed 1.0.

    Lives in the metrics package rather than in ``ratchet_runner``, which the
    2026-09-28 split reduced to orchestration. That split briefly dropped the
    definition while leaving the name in ``ratchet_runner.__all__``, which is how
    a phantom export survives a refactor looking like a live API.
    """
    if not gains or k <= 0:
        return 0.0
    window = list(gains[:k])
    return sum(1 for g in window if g > 0.0) / len(window)

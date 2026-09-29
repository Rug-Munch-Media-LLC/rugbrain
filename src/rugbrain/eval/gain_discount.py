"""Discounted-gain primitives for ranking metrics.

One job: the arithmetic shared by every metric in this package — turn a
sequence of per-rank relevance gains into a DCG, and turn an ideal gain
profile into an IDCG. Both use the 1-indexed logarithmic position discount
``log2(i + 2)`` (rank 1 -> log2(3)? no: rank 1 -> 1/1.0), which is the
convention the deleted legacy ``eval_ragas`` implementation used.

Pure-Python (``math.log2`` only) so the eval harness stays dependency-light
and runs in CI. No I/O, no globals, no imports from the rest of ``rugbrain``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = ["clamp_k", "dcg_at_k", "idcg_binary"]


def clamp_k(k: int | None, n: int) -> int:
    """Resolve a ``None`` cutoff to *n*, then clamp it to the available ranks.

    ``k`` larger than the result list is a request for "everything", not an
    error; a negative or zero cutoff has no window and is rejected by callers.
    """
    return min(k if k is not None else n, n)


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    """Discounted cumulative gain over the first *k* ranks of *gains*."""
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def idcg_binary(n_relevant: int, k: int) -> float:
    """Ideal DCG for a binary-relevance run: the best possible ordering.

    Every relevant result is 1.0 and they occupy the top ranks, so the ideal
    profile is ``[1.0] * min(n_relevant, k)`` padded with zeros. Trailing zeros
    contribute exactly 0.0 to the sum, so they are omitted rather than
    materialised.
    """
    return sum(1.0 / math.log2(i + 2) for i in range(min(n_relevant, k)))

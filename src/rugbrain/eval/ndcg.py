"""
Ranking metrics for labeled retrieval evaluation — NDCG@k and friends.

Pure-Python implementations (``math.log2`` only) so the eval harness stays
dependency-light and runs anywhere, including CI. Formulas:

* ``ndcg_at_k`` — Normalized Discounted Cumulative Gain at rank k. Binary
  relevance (1 if the retrieved id is in the labeled relevant set, else 0),
  logarithmic position discount ``log2(i + 2)`` (1-indexed positions), and an
  ideal DCG computed from the best possible ordering of the relevant ids.
* ``recall_at_k`` — fraction of relevant ids found in the top-k results.
* ``hit_rate_at_k`` — 1.0 if at least one relevant id is in the top-k, else 0.
* ``mrr`` — Mean Reciprocal Rank: 1/rank of the first relevant id.

Conventions (matching the deleted legacy eval_ragas implementation):

* ``ndcg_at_k`` returns ``0.0`` when there is no labeled relevant set (nothing
  to gain credit for).
* ``recall_at_k`` / ``hit_rate_at_k`` / ``mrr`` return ``1.0`` when the
  relevant set is empty (nothing to miss), i.e. the "perfect" score.

[REVIEW] The empty-relevant convention for recall (1.0) is a judgment call
inherited from the legacy pipeline; flip it to 0.0 if you want empty queries
to penalize rather than pass.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """
    NDCG@k over a labeled query -> relevant-doc-id set.

    Args:
        retrieved_ids: ranked result ids, most relevant first.
        relevant_ids: ground-truth relevant ids for the query.
        k: rank cutoff (default: ``len(retrieved_ids)``).

    Returns:
        Float in [0.0, 1.0]; 1.0 when the top-k ranking is exactly the ideal
        ordering of the relevant ids. Returns 0.0 for an empty relevant set.
    """
    if not retrieved_ids or not relevant_ids:
        return 0.0

    rel_set = set(relevant_ids)
    k = min(k if k is not None else len(retrieved_ids), len(retrieved_ids))
    if k <= 0:
        return 0.0

    # Discounted cumulative gain at cutoff k (binary relevance).
    dcg = sum(1.0 / math.log2(i + 2) for i in range(k) if retrieved_ids[i] in rel_set)

    # Ideal DCG: the relevant ids in the best possible order (all 1s first).
    ideal = [1.0] * min(len(rel_set), k) + [0.0] * (k - min(len(rel_set), k))
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def ndcg_from_gains(gains: Sequence[float], k: int | None = None) -> float:
    """
    NDCG@k from per-rank relevance gains (binary 0/1 or graded).

    This is the chunk-mode sibling of ``ndcg_at_k``: instead of matching
    retrieved ids against a labeled relevant-id set, the caller supplies one
    gain per retrieved rank (e.g. 1.0 when a chunk contains a gold phrase,
    else 0.0). The ideal DCG is computed from the best possible ordering of
    the actual gains, so a ranking that puts all relevant chunks first scores
    1.0.

    Args:
        gains: relevance gain per retrieved result, most relevant first.
        k: rank cutoff (default: ``len(gains)``).

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 for an empty sequence or when no
        rank carries positive gain.
    """
    if not gains:
        return 0.0
    k = min(k if k is not None else len(gains), len(gains))
    if k <= 0:
        return 0.0

    window = gains[:k]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(window))
    ideal = sorted((float(g) for g in gains), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def mrr_from_gains(gains: Sequence[float]) -> float:
    """
    Mean Reciprocal Rank from per-rank binary relevance gains.

    Returns 1/rank of the first positive gain (rank is 1-indexed), 0.0 when
    no rank carries positive gain, and 0.0 for an empty sequence. Mirrors
    ``mrr`` but for the chunk/content mode where relevance is judged per
    retrieved result rather than by doc-id membership.
    """
    for i, g in enumerate(gains):
        if g > 0.0:
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """
    Recall@k — fraction of relevant ids present in the top-k results.

    Returns 1.0 when the relevant set is empty (nothing to recall).
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 1.0
    k = k if k is not None else len(retrieved_ids)
    found = sum(1 for rid in rel_set if rid in set(retrieved_ids[:k]))
    return found / len(rel_set)


def hit_rate_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """
    Hit rate@k — 1.0 if any relevant id appears in the top-k, else 0.0.

    Returns 1.0 when the relevant set is empty.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 1.0
    k = k if k is not None else len(retrieved_ids)
    return 1.0 if any(rid in rel_set for rid in retrieved_ids[:k]) else 0.0


def precision_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """
    Precision@k — fraction of the top-k retrieved ids that are relevant.

    Unlike ``recall_at_k`` / ``hit_rate_at_k`` (which treat an empty relevant
    set as a pass), precision is undefined when there is nothing labeled
    relevant, so it returns 0.0 for an empty relevant set. Also returns 0.0
    when ``k`` is 0 or no ids were retrieved.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 0.0
    k = k if k is not None else len(retrieved_ids)
    if k <= 0:
        return 0.0
    window = retrieved_ids[:k]
    if not window:
        return 0.0
    hits = sum(1 for rid in window if rid in rel_set)
    return hits / len(window)


def mrr(retrieved_ids: Sequence[str], relevant_ids: Iterable[str]) -> float:
    """
    Mean Reciprocal Rank — 1/rank of the first relevant id (rank is 1-indexed).

    Returns 0.0 when no relevant id is retrieved; 1.0 when the relevant set is
    empty (nothing to miss).
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 1.0
    for i, doc_id in enumerate(retrieved_ids):
        if doc_id in rel_set:
            return 1.0 / (i + 1)
    return 0.0

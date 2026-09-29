"""Ranking metrics over a retrieved-id list judged against a labeled id set.

This is the *doc-id* family: the caller has ranked document ids and a
ground-truth set of relevant ids, and the metric asks how many of them landed
where. The chunk/content sibling — where relevance is supplied per retrieved
rank instead of per id — lives in :mod:`rugbrain.eval.rank_gains`.

Conventions:

* ``ndcg_at_k`` returns ``0.0`` when there is no labeled relevant set
  (nothing to gain credit for).
* ``recall_at_k`` / ``hit_rate_at_k`` / ``mrr`` return ``1.0`` when the
  relevant set is empty (nothing to miss), i.e. the "perfect" score.
* ``precision_at_k`` returns ``0.0`` for an empty relevant set — precision is
  undefined with nothing labeled relevant, and unlike the others it must not
  read as a pass.

[REVIEW] The empty-relevant convention for recall (1.0) is a judgment call
inherited from the legacy pipeline; flip it to 0.0 if you want empty queries
to penalize rather than pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rugbrain.eval.gain_discount import clamp_k, dcg_at_k, idcg_binary

__all__ = ["hit_rate_at_k", "mrr", "ndcg_at_k", "precision_at_k", "recall_at_k"]


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """NDCG@k — position-discounted credit for the relevant ids in the top-k.

    Returns 1.0 when the top-k is exactly the ideal ordering of the relevant
    ids, 0.0 for an empty relevant set or an empty retrieval.
    """
    if not retrieved_ids or not relevant_ids:
        return 0.0
    rel_set = set(relevant_ids)
    k = clamp_k(k, len(retrieved_ids))
    if k <= 0:
        return 0.0
    gains = [1.0 if rid in rel_set else 0.0 for rid in retrieved_ids[:k]]
    idcg = idcg_binary(len(rel_set), k)
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(gains, k) / idcg


def recall_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """Recall@k — fraction of relevant ids present in the top-k results.

    Returns 1.0 when the relevant set is empty (nothing to recall).
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 1.0
    k = clamp_k(k, len(retrieved_ids))
    found = sum(1 for rid in rel_set if rid in set(retrieved_ids[:k]))
    return found / len(rel_set)


def hit_rate_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """Hit rate@k — 1.0 if any relevant id appears in the top-k, else 0.0.

    Returns 1.0 when the relevant set is empty.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 1.0
    k = clamp_k(k, len(retrieved_ids))
    return 1.0 if any(rid in rel_set for rid in retrieved_ids[:k]) else 0.0


def precision_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """Precision@k — fraction of the top-k retrieved ids that are relevant.

    Returns 0.0 (not 1.0) for an empty relevant set, and 0.0 when k is 0, k is
    negative, or nothing was retrieved.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return 0.0
    k = clamp_k(k, len(retrieved_ids))
    window = retrieved_ids[:k]
    if k <= 0 or not window:
        return 0.0
    return sum(1 for rid in window if rid in rel_set) / len(window)


def mrr(retrieved_ids: Sequence[str], relevant_ids: Iterable[str]) -> float:
    """Mean Reciprocal Rank — 1/rank of the first relevant id (1-indexed).

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

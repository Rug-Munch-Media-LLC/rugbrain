"""Ranking metrics for labeled retrieval evaluation — NDCG@k and friends.

Public facade over the three one-job modules that hold the implementations.
Kept as a module (rather than deleted) because ``rugbrain.eval.__init__`` and
the test suite import these names from here.

Layout:

* :mod:`gain_discount` — the shared discounted-gain arithmetic.
* :mod:`rank_ids`      — metrics judged against a labeled relevant-id set.
* :mod:`rank_gains`    — metrics judged by a per-rank relevance gain.

Pure-Python (``math.log2`` only) so the eval harness stays dependency-light
and runs anywhere, including CI.
"""

from __future__ import annotations

from rugbrain.eval.gain_discount import clamp_k, dcg_at_k, idcg_binary
from rugbrain.eval.rank_gains import mrr_from_gains, ndcg_from_gains, recall_from_gains
from rugbrain.eval.rank_ids import (
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

# Re-exported from the implementation modules. Every name here is bound by an
# import statement above, so `__all__` cannot name a phantom.
__all__ = [
    "clamp_k",
    "dcg_at_k",
    "hit_rate_at_k",
    "idcg_binary",
    "mrr",
    "mrr_from_gains",
    "ndcg_at_k",
    "ndcg_from_gains",
    "precision_at_k",
    "recall_at_k",
    "recall_from_gains",
]

"""
rugbrain.eval — labeled retrieval/rerank/hybrid evaluation harness.

Pure-Python metric math (``ndcg``), lightweight RAG-quality metrics without
the heavy ``ragas`` dependency (``metrics``), a runnable CLI (``run_eval``)
that scores a search function against a labeled JSONL dataset, and a
retrieval-only runner (``retrieval_eval``) that measures hit@k / recall@k /
precision@k plus p50/p95 latency against the live RAG search endpoint.

Modules are deliberately isolated from the search lane so eval changes never
touch retrieval/ingest code:

* ``ndcg`` — NDCG@k, recall@k, hit_rate@k, precision@k, MRR.
* ``metrics`` — faithfulness, answer relevance, context precision/recall.
* ``run_eval`` — ``python -m rugbrain.eval.run_eval --dataset <jsonl> --top-k 5``.
* ``retrieval_eval`` — ``python -m rugbrain.eval.retrieval_eval`` (live endpoint).
* ``sample_dataset.jsonl`` / ``golden_set.jsonl`` — runnable crypto datasets.

``run_eval`` / ``retrieval_eval`` names are exposed lazily (PEP 562) so
``import rugbrain.eval`` stays dependency-light and ``python -m
rugbrain.eval.run_eval`` does not trip runpy's "found in sys.modules after
import of package" warning.
"""

from __future__ import annotations

from typing import Any

from rugbrain.eval.metrics import (
    answer_relevance,
    context_precision,
    context_recall,
    faithfulness,
    ragas_available,
)
from rugbrain.eval.ndcg import hit_rate_at_k, mrr, ndcg_at_k, precision_at_k, recall_at_k

__all__ = [  # type: ignore[reportUnsupportedDunderAll]
    "EvalSummary",
    "QueryEvalResult",
    "QueryExample",
    "answer_relevance",
    "context_precision",
    "context_recall",
    "faithfulness",
    "hit_rate_at_k",
    "load_dataset",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "ragas_available",
    "recall_at_k",
    "run_eval",
]

# Names re-exported from the CLI module, imported on first attribute access.
_LAZY_NAMES = frozenset(
    {"EvalSummary", "QueryEvalResult", "QueryExample", "load_dataset", "run_eval"}
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_NAMES:
        from rugbrain.eval import run_eval

        return getattr(run_eval, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_NAMES))

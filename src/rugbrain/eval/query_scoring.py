"""Score ONE golden query against a retriever.

Split out of ``harness`` because "how is a single ranked result list graded"
is a different question from "run a whole golden set and aggregate", and they
change for different reasons. The two grading modes are also kept apart here
because they fail differently:

    doc-id mode  exact ``relevant_doc_ids`` match. Precise, but the ids are
                  UUIDs that are NOT stable across re-ingests.
    chunk mode   case-insensitive substring of ``gold_chunks`` in ``content``.
                  Robust to re-ingest, but it can match on vocabulary rather
                  than on meaning — so a graded run that leans on chunk mode
                  must say so, or the number is softer than it looks.

A retriever exception is captured into ``QueryScore.error`` rather than raised,
so one bad query cannot abort a set. That fail-open is a HARNESS property: the
gate layer (``missioneval.gate``) inverts it, because a run that errored on
every case must never be readable as a pass.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from rugbrain.eval.backtest_report import QueryScore
from rugbrain.eval.golden_set import GoldenQuery, item_is_relevant, result_id
from rugbrain.eval.ndcg import mrr, mrr_from_gains, ndcg_at_k, ndcg_from_gains, recall_at_k

logger = logging.getLogger(__name__)


async def score_query(
    retriever: Callable[..., Any],
    example: GoldenQuery,
    top_k: int,
    k_values: Iterable[int],
) -> QueryScore:
    """Run one golden query through *retriever* and score the ranking.

    ``retriever`` is awaited as ``retriever(query, collection, limit)``. Any
    exception is captured into ``error`` so one bad query cannot abort the run.
    """
    score = QueryScore(query=example.query, collection=example.collection)
    start = time.perf_counter()
    try:
        items = await retriever(example.query, example.collection, top_k)
        score.retrieved_ids = [result_id(item) for item in (items or [])]
        score.latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # pragma: no cover - defensive, fail-open
        logger.debug("harness: retrieval failed for %r: %s", example.query, exc)
        score.error = f"{type(exc).__name__}: {exc}"
        score.latency_ms = (time.perf_counter() - start) * 1000.0
        return score

    if example.relevant_doc_ids:
        # Doc-id mode: standard retrieval metrics against labeled ids.
        relevant: list[str] = list(example.relevant_doc_ids)
        score.relevant_ids = relevant
        for k in k_values:
            score.recall[k] = recall_at_k(score.retrieved_ids, relevant, k=k)
            score.ndcg[k] = ndcg_at_k(score.retrieved_ids, relevant, k=k)
        score.mrr_value = mrr(score.retrieved_ids, relevant)
        return score

    _score_by_content(score, example, items or [], top_k, k_values)
    return score


def _score_by_content(
    score: QueryScore,
    example: GoldenQuery,
    items: list[dict[str, Any]],
    top_k: int,
    k_values: Iterable[int],
) -> None:
    """Chunk mode: relevance is judged per retrieved rank by content match.

    * recall@k = fraction of gold chunks surfaced in the top-k
    * nDCG@k / MRR = ranking quality over binary per-rank gains
    """
    top_items = items[:top_k]
    matched: set[str] = set()
    for item in top_items:
        content = (item.get("content") or "").lower()
        for chunk in example.gold_chunks:
            if chunk.lower() in content:
                matched.add(chunk)
    score.relevant_ids = sorted(matched)
    gains = [1.0 if item_is_relevant(item, example) else 0.0 for item in top_items]
    for k in k_values:
        score.recall[k] = (
            len(
                {
                    c
                    for c in example.gold_chunks
                    if any(
                        c.lower() in (item.get("content") or "").lower() for item in top_items[:k]
                    )
                }
            )
            / len(example.gold_chunks)
            if example.gold_chunks
            else 0.0
        )
        score.ndcg[k] = ndcg_from_gains(gains, k=k)
    score.mrr_value = mrr_from_gains(gains)


__all__ = ["score_query"]

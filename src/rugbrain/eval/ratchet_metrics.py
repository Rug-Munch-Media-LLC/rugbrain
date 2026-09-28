"""Corpus identity and hit scoring for the retrieval ratchet.

Split from the 384-line `ratchet_runner` on 2026-09-28. One job: given a golden
row and the hits a layer returned, produce the numbers — plus the corpus hash
that pins "same corpus" for a baseline comparison.

This module also exists because a `recall_from_gains` reference survived a
previous split as a phantom `__all__` entry with no implementation behind it,
which is how a dead export reads as a live API.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Any

from rugbrain.eval.baseline_ratchet import LayerMetrics
from rugbrain.eval.ndcg import mrr_from_gains, ndcg_from_gains

logger = logging.getLogger(__name__)

_SKIP_KEYS = frozenset({"__comment__", "notes"})

def corpus_hash(rows: list[dict[str, Any]]) -> str:
    """Fingerprint the measured corpus: ids, queries and gold, sorted.

    A baseline is only comparable against the same corpus. Hashing the
    *content* (not the file mtime) means a reformat that changes nothing
    measurable does not invalidate the baseline, while a real edit — a gold
    chunk rewritten, a case added — does.
    """
    payload = sorted(
        json.dumps(
            {
                "query": r.get("query", ""),
                "collection": r.get("collection"),
                "gold_chunks": sorted(r.get("gold_chunks") or []),
                "relevant_doc_ids": sorted(r.get("relevant_doc_ids") or []),
            },
            sort_keys=True,
        )
        for r in rows
        if not (_SKIP_KEYS & set(r))
    )
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()


def _gold_ids(row: dict[str, Any]) -> list[str]:
    return [str(i) for i in (row.get("relevant_doc_ids") or []) if i]


def _gold_text(row: dict[str, Any]) -> list[str]:
    return [str(c) for c in (row.get("gold_chunks") or []) if c]


def _hit_id(hit: dict[str, Any]) -> str:
    for key in ("id", "doc_id", "uuid", "chunk_id"):
        val = hit.get(key)
        if val is not None:
            return str(val)
    return ""


def _hit_text(hit: dict[str, Any]) -> str:
    return str(hit.get("content") or hit.get("text") or "")


def _score_hits(
    row: dict[str, Any], hits: list[dict[str, Any]], k_values: tuple[int, ...]
) -> dict[str, float]:
    """Score one query's ranked hits against its gold.

    Exact id match when the row carries ``relevant_doc_ids``; otherwise a
    case-insensitive substring match of ``gold_chunks`` against the hit text.
    Ids are not stable across re-ingests, so text matching is the fallback
    rather than a silently weaker default.

    Gains family, not ids family
    -----------------------------
    ``recall_at_k``/``mrr``/``ndcg_at_k`` are the ID-SCORING functions: they
    expect ``(retrieved_ids, relevant_ids)`` and answer "how many of the gold
    documents did we find?". Passing the per-result boolean list as BOTH
    arguments made the relevant set the entire retrieved list, so every hit
    counted as gold:

      * recall became ``|retrieved| / |retrieved|`` = 1.0 always,
      * mrr became 1.0 whenever anything was relevant at all,
      * nDCG divided DCG by an IDCG built from the *retrieved* length, and
        exceeded 1.0 — measured 1.81 for a single relevant doc at rank 1, a
        quantity that is bounded by 1.0 by definition.

    That is why the first real run reported recall@5=0.917, mrr=1.000 and
    ndcg@10=3.209. Those numbers were not retrieval quality; they were the
    metric scoring itself.

    The ``*_from_gains`` family takes exactly what this code produces — one
    gain per retrieved result — and derives IDCG from the ACTUAL gains, which
    is the only variant bounded by construction.
    """
    gold_ids = set(_gold_ids(row))
    gold_text = [g.lower() for g in _gold_text(row)]
    gains: list[float] = []
    for hit in hits:
        if gold_ids:
            gains.append(1.0 if _hit_id(hit) in gold_ids else 0.0)
            continue
        haystack = _hit_text(hit).lower()
        gains.append(1.0 if any(g and g in haystack for g in gold_text) else 0.0)

    out: dict[str, float] = {}
    for k in k_values:
        out[f"recall_at_{k}"] = recall_from_gains(gains, k)
    out["mrr"] = mrr_from_gains(gains)
    out["ndcg_at_10"] = ndcg_from_gains(gains, 10)
    return out


def recall_from_gains(gains: Sequence[float], k: int) -> float:
    """Fraction of the top-``k`` results that are relevant.

    Bounded by construction: the numerator is a subset of the denominator, so
    unlike the id-family recall — where an empty relevant set returns 1.0 and a
    mismatched set returns nonsense — this can never exceed 1.0.
    """
    if not gains or k <= 0:
        return 0.0
    window = list(gains[:k])
    return sum(1 for g in window if g > 0.0) / len(window)


def _average(values: list[dict[str, float]]) -> LayerMetrics:
    if not values:
        return LayerMetrics()
    keys = values[0].keys()
    return LayerMetrics(**{k: round(sum(v[k] for v in values) / len(values), 4) for k in keys})

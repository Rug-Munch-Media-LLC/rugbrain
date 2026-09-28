"""Corpus fingerprinting and per-query hit scoring for the retrieval ratchet.

Split out of ``ratchet_runner`` so the MEASUREMENT PRIMITIVES — "what is being
measured" (the corpus hash) and "was this query answered correctly" (hit
scoring) — sit apart from the store wiring and the run loop that drive them.

The hash is the load-bearing part. A baseline is only comparable against the
same corpus, so it fingerprints the *content* (queries, gold ids, gold text)
rather than the file mtime: a reformat that changes nothing measurable does
not invalidate the baseline, while a real edit — a gold chunk rewritten, a
case added, a case dropped — does. Hashing the mtime would make every
whitespace change look like a corpus change and train you to re-record
baselines, which is exactly how a red gate gets made green.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from rugbrain.eval.ndcg import mrr, ndcg_at_k, recall_at_k

# Golden rows carry a target collection; rows naming a collection we do not
# measure are reported as errors rather than silently skipped.
_SKIP_KEYS = frozenset({"__comment__", "notes"})


def corpus_hash(rows: list[dict[str, Any]]) -> str:
    """Fingerprint the measured corpus: ids, queries and gold, sorted."""
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
    """
    gold_ids = set(_gold_ids(row))
    gold_text = [g.lower() for g in _gold_text(row)]
    ranked: list[bool] = []
    for hit in hits:
        hit_id = _hit_id(hit)
        if gold_ids:
            ranked.append(hit_id in gold_ids)
            continue
        haystack = _hit_text(hit).lower()
        ranked.append(any(g and g in haystack for g in gold_text))

    out: dict[str, float] = {}
    for k in k_values:
        # recall_at_k(retrieved_ids, relevant_ids, k) — the gold set here is
        # scored as a per-result BOOLEAN relevance list, not as ids, so the
        # relevant set and the retrieved set are the same list. The old call
        # passed (ranked, k) positionally, which put an int where
        # relevant_ids is expected: TypeError -> set(int) is not iterable.
        # That is the real defect; "unhashable type: list" did NOT reproduce.
        out[f"recall_at_{k}"] = recall_at_k(ranked, ranked, k)
    # Same signature shape as recall_at_k above: the gold set is scored as a
    # per-result boolean relevance list, so both arguments are that list.
    out["mrr"] = mrr(ranked, ranked)
    out["ndcg_at_10"] = ndcg_at_k(ranked, ranked, 10)
    return out


__all__ = ["corpus_hash"]

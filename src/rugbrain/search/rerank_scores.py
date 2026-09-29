"""Pure helpers for turning cross-encoder results back into candidate scores.

The search-lane ladder asks each rung for ``{index, relevance_score}`` rows
over a *capped* subset of candidates, then has to scatter those scores back
across the full candidate list. Doing that three times inline is where the
budget bugs lived, so it lives here once.

No module state: everything takes its inputs as arguments.
"""

from __future__ import annotations

import os
from typing import Any

from rugbrain.token_budget import clip, record

# Max content chars fed to the cross-encoder per chunk (truncation guard).
# Server ctx raised 4096 -> 8192 (2026-08-22); query+doc must fit the budget,
# so ~6000 chars of doc text is safe for typical queries.
RERANK_TEXT_LIMIT = 6000

# ── Token budget (measured 2026-09-28) ─────────────────────────────────
# The 6000-char cap above is 2142 tokens (measured corpus ratio 0.357
# tok/char), which is 2.8x more cross-encoder context than a rerank decision
# needs. Measured on the content path: the top-12 news_v2 chunks place 69.8%
# of their tokens in the first 400 chars, and 768 tok covers p99 of every
# collection measured (known_scams p99=744). See ``rugbrain.token_budget``.
TOKEN_BUDGET_CAP = int(os.getenv("RUGBRAIN_TOKEN_RERANK", "768"))

_SCORE_KEYS = ("score", "rrf_score", "similarity", "bm25_score", "text_score")
_TEXT_KEYS = ("content", "text", "chunk_text", "title", "summary")


def original_score(doc: dict[str, Any]) -> float:
    """Best available retrieval score for a chunk (for candidate selection)."""
    for key in _SCORE_KEYS:
        val = doc.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def chunk_text(doc: dict[str, Any]) -> str:
    """Extract searchable text from a chunk dict, clamped to the rerank budget.

    The char cap stays as a cheap pre-filter (it avoids handing a 200k-char
    blob to the tokenizer); the token cap is what actually bounds what the
    cross-encoder sees.
    """
    for key in _TEXT_KEYS:
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            clipped = clip(val[:RERANK_TEXT_LIMIT], TOKEN_BUDGET_CAP)
            record("rerank_doc", clipped)
            return clipped
    return ""


def valid_docs(candidates: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """Budget-clipped ``(original_index, text)`` pairs, blanks dropped."""
    docs = [chunk_text(d) for d in candidates]
    return [(i, t) for i, t in enumerate(docs) if t]


def scatter(
    results: list[dict[str, Any]],
    scored_subset: list[tuple[int, str]],
    total: int,
) -> list[float]:
    """Map rung results over a capped subset back onto a full-length score list.

    ``scored_subset[i]`` is ``(original_candidate_index, text)`` for position
    ``i`` in what was actually sent, so a rung echoing ``index=2`` writes to
    ``scored_subset[2][0]``. Candidates that were capped out — and positions
    the rung omitted — keep 0.0 rather than being silently misaligned.
    """
    scores: list[float] = [0.0] * total
    for r in results:
        pos = r.get("index")
        if isinstance(pos, int) and 0 <= pos < len(scored_subset):
            scores[scored_subset[pos][0]] = float(r.get("relevance_score", 0.0))
    return scores

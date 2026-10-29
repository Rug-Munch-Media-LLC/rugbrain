"""corpus_map / corpus_ask — minimal-context awareness of our OWN corpus.

Both tools read live state: corpus_map from Qdrant, corpus_ask through the
existing /api/v1/search path. Neither falls back to a static manifest, and
neither pads an answer it cannot cite.
"""

from __future__ import annotations

from typing import Any

from rugbrain.rugbrain_mcp.app import mcp


@mcp.tool(name="corpus_map")
async def corpus_map(detail: str = "brief") -> dict[str, Any]:
    """Live map of every corpus collection — call this before guessing what we hold.

    Returns, per collection: points, dense dim, freshness (age of newest
    timestamped payload), the one question it answers, which tool/route exposes
    it, and top sample titles. detail='brief' emits a <=40-line text block
    (token-cheap); 'full' adds payload keys and 5 samples. Qdrant unreachable
    returns status='refused' with a typed error, never an empty list.
    """
    from rugbrain.corpus_awareness import HttpQdrantProbe, build_corpus_map

    return await build_corpus_map(HttpQdrantProbe(), detail=detail)


@mcp.tool(name="corpus_ask")
async def corpus_ask(question: str, budget_tokens: int = 800) -> dict[str, Any]:
    """Ask our corpus a question and get CITED hits, not a guess.

    Routes by intent (exploit / code / our-own-repo / news) to the declared
    collections, searches each, merges + dedupes by story key, and returns
    marks of the form [n] collection | title | score under budget_tokens.
    Returns status='insufficient_evidence' when nothing clears the relevance
    floor, and status='refused' if Qdrant or the search lane is unreachable.
    """
    from rugbrain.corpus_awareness import HttpQdrantProbe, HttpSearch, ask_corpus

    try:
        available = await HttpQdrantProbe().collections()
    except Exception:
        available = None
    return await ask_corpus(
        question, HttpSearch(), budget_tokens=budget_tokens, available=available
    )

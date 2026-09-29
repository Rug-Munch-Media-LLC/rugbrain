"""Task-specific rerank instructions for the decoupled Qwen3 client.

Qwen3-Reranker is instruction-aware, so a per-task prompt template measurably
changes the ranking. These helpers own the template library and the three
callers that consume it (alerts, KOL tweets, generic task types). Ranking
itself lives in ``rugbrain.reranker``; the search-lane instruction library is a
separate concern and lives in ``rugbrain.search.rerank_instructions``.
"""

from __future__ import annotations

from typing import Any

# Instruction templates for per-task reranking
RERANK_INSTRUCTIONS: dict[str, str] = {
    "content_digest": (
        "Rank these crypto market signals by importance for a trader dashboard "
        "prioritizing: unusual whale activity, imminent exchange risks, "
        "liquidation cascades, and DeFi protocol anomalies over routine price movements"
    ),
    "alert_filter": (
        "Identify which of these alerts represents a real imminent threat requiring "
        "immediate attention versus routine market activity noise. Prioritize: "
        "exchange withdrawal suspensions, large whale movements to exchanges, "
        "oracle price deviations, and protocol insolvency signals"
    ),
    "kol_scam": (
        "Analyze this KOL's tweet for signs of scam promotion: excessive hype "
        "without substance, shilling low-cap tokens, coordinated engagement "
        "patterns, promises of guaranteed returns, and pressure to act quickly. "
        "Flag as suspicious if multiple scam indicators are present"
    ),
    "search": (
        "Rank these documents by relevance to the user's search intent "
        "prioritizing: specific data points, actionable intelligence, recent "
        "events, and verified on-chain activity over general commentary"
    ),
    "wallet_risk": (
        "Evaluate the risk level of these wallet movements prioritizing: "
        "funds moving to exchanges known for wash trading, large outflows from "
        "shady exchanges, transfers to mixers or privacy wallets, and "
        "accumulation by known scam-affiliated addresses"
    ),
}


async def rerank_instructed(
    task_type: str,
    query: str,
    documents: list[str],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Rerank with instruction-aware prompt for specific task types.

    Args:
        task_type: One of 'content_digest', 'alert_filter', 'kol_scam', 'search', 'wallet_risk'.
        query: The search query.
        documents: Documents to rank.
        top_k: Number of results to return.

    Uses instruction template to customize scoring per task.
    Falls back to standard rerank if task_type is unknown.
    """
    from rugbrain.reranker import rerank

    instruction = RERANK_INSTRUCTIONS.get(task_type)
    if instruction:
        # Instruction-aware: prepend instruction to query
        instructed_query = f"[INST] {instruction} [/INST] Query: {query}"
        return await rerank(instructed_query, documents, top_k=top_k)
    return await rerank(query, documents, top_k=top_k)


def _attach_scores(
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
    score_key: str,
) -> list[dict[str, Any]]:
    """Map ``{index, relevance_score}`` results back onto the source items."""
    scored: list[dict[str, Any]] = []
    for r in results:
        idx = r["index"]
        if idx < len(items):
            item = dict(items[idx])
            item[score_key] = r["relevance_score"]
            scored.append(item)
    scored.sort(key=lambda x: x.get(score_key, 0), reverse=True)
    return scored


async def rerank_alerts(
    alerts: list[dict[str, Any]],
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """Score alerts by real threat level using instruction-aware reranking.

    Args:
        alerts: List of alert dicts with 'title' and 'message' keys.
        top_k: Return top-k ranked alerts.

    Returns alerts sorted by threat score descending.
    """
    if not alerts:
        return []

    docs = [f"{a.get('title', '')}: {str(a.get('message', ''))[:200]}" for a in alerts]
    results = await rerank_instructed(
        "alert_filter",
        "Which alerts represent real imminent threats?",
        docs,
        top_k=top_k,
    )
    return _attach_scores(alerts, results, "_rerank_score")


async def rerank_kol_tweets(
    tweets: list[dict[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Score KOL tweets by scam likelihood.

    Args:
        tweets: List of tweet dicts with 'text' and 'author' keys.
        top_k: Return top-k most suspicious tweets.

    Returns tweets sorted by scam score descending.
    """
    if not tweets:
        return []

    docs = [f"@{t.get('author', '')}: {t.get('text', '')[:200]}" for t in tweets]
    results = await rerank_instructed(
        "kol_scam",
        "Which tweets show signs of scam promotion?",
        docs,
        top_k=top_k,
    )
    return _attach_scores(tweets, results, "_scam_score")

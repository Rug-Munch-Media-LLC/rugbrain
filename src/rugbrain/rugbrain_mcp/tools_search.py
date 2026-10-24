"""Search tools for the rugbrain-mcp facade (split out of server.py)."""

from __future__ import annotations

from typing import Any

from rugbrain.rugbrain_mcp.app import _call, mcp


@mcp.tool(name="rugbrain_search")
def rugbrain_search(
    query: str,
    collection: str = "threat_intel_v2",
    limit: int = 10,
    rerank: bool = True,
) -> dict[str, Any]:
    """Semantic search across RugBrain collections (news, scams, incidents, audits).

    Args:
        query: Search query — token address, name, scam description, or topic.
        collection: Qdrant collection to search (default: threat_intel_v2).
            Options: threat_intel_v2, defi_exploits, scam_intel_v2, known_scams,
            scam_patterns, news_v2, wallet_v2, contract_audits,
            crypto_vulnerabilities, regulation_enforcement, education.
        limit: Max results (1-100, default 10).
        rerank: Re-rank top-20 with Qwen3-Reranker-8B (default true).
    """
    return _call(
        "/api/v1/search",
        {
            "q": query,
            "collection": collection,
            "limit": max(1, min(limit, 100)),
            "rerank": "true" if rerank else "false",
        },
    )


@mcp.tool(name="rugbrain_search_multi")
def rugbrain_search_multi(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    limit_per_collection: int = 20,
    rerank: bool = True,
) -> dict[str, Any]:
    """Search across multiple Qdrant collections in parallel, fuse with RRF.

    Args:
        query: Search query text.
        collections: Collections to search (default: ["threat_intel_v2"]).
        limit: Final fused results (1-100, default 10).
        limit_per_collection: Per-collection limit before fusion (1-100, default 20).
        rerank: Re-rank top-40 fused candidates with Qwen3-Reranker-8B (default true).
    """
    return _call(
        "/api/v1/search/multi",
        body={
            "query": query,
            "collections": collections or ["threat_intel_v2"],
            "limit": max(1, min(limit, 100)),
            "limit_per_collection": max(1, min(limit_per_collection, 100)),
            "rerank": rerank,
        },
    )

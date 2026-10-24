"""Investigation / intel tools for the rugbrain-mcp facade (split out of server.py)."""

from __future__ import annotations

from typing import Any

from rugbrain.rugbrain_mcp.app import _call, mcp


@mcp.tool(name="rugbrain_analyze")
def rugbrain_analyze(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Full risk analysis for an address (3-pillar: scam/wallet/contract).

    Args:
        address: Token, contract, or wallet address.
        chain: Chain of the address (default: ethereum).
    """
    return _call("/api/v1/rag/v2/scam-check", {"address": address, "chain": chain})


@mcp.tool(name="rugbrain_scam_check")
def rugbrain_scam_check(
    address: str, chain: str = "ethereum", limit: int = 10, tier: str = "free"
) -> dict[str, Any]:
    """Scam-check an address (RugBrain 3-pillar verdict).

    Args:
        address: Token, contract, or wallet address.
        chain: Chain (default: ethereum).
        limit: Max matches (1-100, default 10).
        tier: free or pro (default: free).
    """
    return _call(
        "/api/v1/rag/v2/scam-check",
        {"address": address, "chain": chain, "limit": max(1, min(limit, 100)), "tier": tier},
    )


@mcp.tool(name="rugbrain_wallet_profile")
def rugbrain_wallet_profile(address: str, max_hops: int = 2) -> dict[str, Any]:
    """Entity investigation for an address: labels, risk verdict, related intel.

    Looks the address up by exact match across the wallet/scam label corpora
    (wallet_v2_8b, wallet_profiles_8b, known_scams_8b) and returns an auditable
    verdict. ``found=false`` means the address is UNSEEN, not clean.

    Args:
        address: Wallet/contract address to investigate.
        max_hops: Max graph traversal depth (1-5, default 2). Recorded for
            compatibility; the label corpora are flat.
    """
    return _call(
        "/api/v1/rag/investigate",
        body={"query": address, "type": "wallet", "max_hops": max(1, min(max_hops, 5))},
    )


@mcp.tool(name="rugbrain_alerts")
def rugbrain_alerts(minutes: int = 60) -> dict[str, Any]:
    """Recent alerts detected in the last N minutes.

    Args:
        minutes: Lookback window (1-1440, default 60).
    """
    return _call("/api/v1/alerts/recent", {"minutes": max(1, min(minutes, 1440))})


@mcp.tool(name="rugbrain_news")
def rugbrain_news(
    query: str = "",
    limit: int = 20,
    sources: str = "youtube,podcast,newsletter,news",
    category: str | None = None,
    min_date: str | None = None,
) -> dict[str, Any]:
    """Unified news feed across YouTube, podcasts, newsletters, and news.

    Args:
        query: Search query across all sources (default: empty = latest).
        limit: Max results (1-100, default 20).
        sources: Comma-separated sources (default: all).
        category: Filter by category (optional).
        min_date: ISO date filter YYYY-MM-DD (optional).
    """
    params: dict[str, Any] = {"q": query, "limit": max(1, min(limit, 100)), "sources": sources}
    if category:
        params["category"] = category
    if min_date:
        params["min_date"] = min_date
    return _call("/api/v1/feed/all", params)


@mcp.tool(name="rugbrain_sentiment")
def rugbrain_sentiment(
    address: str,
    chain: str = "ethereum",
    include_social: bool = True,
    include_news: bool = True,
) -> dict[str, Any]:
    """Aggregate sentiment for a token from RAG collections.

    Args:
        address: Token/contract address.
        chain: Chain (default: ethereum).
        include_social: Include social_intel collection (default true).
        include_news: Include news_articles collection (default true).
    """
    return _call(
        f"/api/v1/sentiment/token/{address}",
        {
            "chain": chain,
            "include_social": str(include_social).lower(),
            "include_news": str(include_news).lower(),
        },
    )


@mcp.tool(name="rugbrain_vector_landscape")
def rugbrain_vector_landscape() -> dict[str, Any]:
    """Live map of every vector space in the fleet: collections, dims, point counts, mismatch flags."""
    return _call("/api/v1/search/vector-landscape")


@mcp.tool(name="rugbrain_health")
def rugbrain_health(full: bool = False) -> dict[str, Any]:
    """RugBrain health endpoints.

    Args:
        full: If true, call /api/v1/health/full; otherwise /health and /ready.
    """
    if full:
        return _call("/api/v1/health/full")
    return _call("/health")


# ── Market-specialist surface (moved in 2026-10-12) ──────────────────

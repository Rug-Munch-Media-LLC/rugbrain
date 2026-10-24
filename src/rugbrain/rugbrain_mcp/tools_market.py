"""Async market-specialist tools for the rugbrain-mcp facade."""

from __future__ import annotations

import re
from typing import Any

from fastmcp.tools import ToolResult
from pydantic import Field

from rugbrain.rugbrain_mcp.app import mcp
from rugbrain.rugbrain_mcp.market_helpers import (
    _call_search_multi,
    _extract_results,
    _source_of,
)


@mcp.tool()
async def rugbrain_social_sentiment(
    query: str = Field(description="Topic, ticker or theme to read the social/narrative mood on."),
    limit: int = Field(default=10, ge=1, le=100, description="Maximum items to return."),
) -> ToolResult:
    """Social + narrative read for a topic or ticker.

    Social intel first (crypto tweets with sentiment labels), news second.
    Distinct from ``rugbrain_sentiment``, which aggregates sentiment for a
    TOKEN ADDRESS from on-chain/social/news sources.
    """
    resp = await _call_search_multi(
        query=query,
        collections=["social_intel", "news_v2"],
        limit=limit,
        rerank=True,
    )
    return ToolResult(
        content=[
            {
                "type": "text",
                "text": "\n".join(
                    f"[{_source_of(item)}] {item.get('content', '')[:300]}"
                    for item in resp.get("results", [])
                ),
            }
        ],
        structured_content={
            "query": query,
            "limit": limit,
            "results": [
                {
                    "content": item.get("content", ""),
                    "score": float(item.get("score", 0.0)),
                    # one cited source per item (singular): the answer shape
                    # promises collection + score, so a plural key would be a
                    # second, weaker contract.
                    "collection": _source_of(item),
                }
                for item in resp.get("results", [])
            ],
        },
    )


@mcp.tool()
async def rugbrain_market_brief(
    query: str = Field(
        default="",
        description="Optional query to focus the brief (e.g. 'Bitcoin', 'DeFi'). "
        "If empty, returns a general market brief.",
    ),
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of items to return.",
    ),
) -> ToolResult:
    """What's happening in crypto right now.

    Fuses news_v2, social_intel, and education collections, reranked,
    each item carrying its source collection and relevance score.
    """
    collections = ["news_v2", "social_intel", "education"]
    # /api/v1/search/multi short-circuits an empty query to {"results": []}, so
    # the documented "empty = general market brief" default returned nothing.
    # A general brief is a real query, not an empty one.
    effective_query = query.strip() or "crypto market news today"
    all_items: list[dict[str, Any]] = []
    for coll in collections:
        resp = await _call_search_multi(
            query=effective_query,
            collections=[coll],
            limit=limit,
            limit_per_collection=limit,
            rerank=True,
        )
        all_items.extend(_extract_results(resp, coll))
    all_items.sort(key=lambda x: x["score"], reverse=True)
    return ToolResult({"brief": all_items[:limit], "query": query, "limit": limit})


@mcp.tool()
async def rugbrain_scam_radar(
    query: str = Field(
        description="Token, address, or topic (e.g. '0x123...', 'BONK', 'pump and dump').",
    ),
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of scam / shill indicators to return.",
    ),
) -> ToolResult:
    """Scam / shill read for a token, address, or topic.

    Searches scam_patterns_8b, scam_intel_v2, and threat_intel_v2,
    reranked. Flags shill categories and extracts contract addresses.
    """
    collections = ["scam_patterns_8b", "scam_intel_v2", "threat_intel_v2"]
    addr_re = re.compile(r"0x[a-fA-F0-9]{40}")
    all_items: list[dict[str, Any]] = []
    for coll in collections:
        resp = await _call_search_multi(
            query=query,
            collections=[coll],
            limit=limit,
            limit_per_collection=limit,
            rerank=True,
        )
        for item in resp.get("results", []):
            content = item.get("content", "")
            all_items.append(
                {
                    "content": content,
                    "collection": coll,
                    "score": float(item.get("score", 0.0)),
                    "addresses": addr_re.findall(content),
                }
            )
    all_items.sort(key=lambda x: x["score"], reverse=True)
    return ToolResult({"results": all_items[:limit], "query": query, "limit": limit})

"""Async market-specialist tools for the rugbrain-mcp facade."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastmcp.tools import ToolResult
from pydantic import Field

from rugbrain.result_quality import dedupe_by_content
from rugbrain.rugbrain_mcp.app import mcp
from rugbrain.rugbrain_mcp.market_helpers import (
    _call_search_multi,
    _extract_results,
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
    # The served collections are the _8b twins (4096-d). The bare names are
    # pre-migration shells: social_intel holds 1 point, news_v2 24, so a query
    # answered "no social signal" from an empty collection.
    resp = await _call_search_multi(
        query=query,
        collections=["social_intel_8b", "news_v2_8b"],
        limit=limit,
        rerank=True,
    )
    items = dedupe_by_content(_extract_results(resp, "social_intel_8b"))
    return ToolResult(
        content=[
            {
                "type": "text",
                "text": "\n".join(
                    f"[{item['collection']}] {item['content'][:300]}" for item in items
                ),
            }
        ],
        structured_content={
            "query": query,
            "limit": limit,
            # _extract_results reads rerank_score / rrf_score / similarity. The
            # API never emits "score", so reading it directly returned 0.0 for
            # every item — the tool answered "all zeros" with a 200.
            "results": items,
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
    # _8b twins: education does not exist at all under the bare name.
    collections = ["news_v2_8b", "social_intel_8b", "education_8b"]
    # /api/v1/search/multi short-circuits an empty query to {"results": []}, so
    # the documented "empty = general market brief" default returned nothing.
    # A general brief is a real query, not an empty one.
    effective_query = query.strip() or "crypto market news today"
    # Three SEQUENTIAL reranked searches took 3 x 8-15 s and the third hit the
    # 30 s client timeout, which surfaced as "MCP server process failed". Run
    # the legs concurrently and without rerank: rerank is worth ~+0.008 MRR on
    # editorial news (docs/RERANK-VERDICT.md) and costs ~10x latency.
    responses = await asyncio.gather(
        *[
            _call_search_multi(
                query=effective_query,
                collections=[coll],
                limit=limit,
                limit_per_collection=limit,
                rerank=False,
            )
            for coll in collections
        ]
    )
    all_items: list[dict[str, Any]] = []
    for coll, resp in zip(collections, responses, strict=True):
        all_items.extend(_extract_results(resp, coll))
    all_items.sort(key=lambda x: x["score"], reverse=True)
    # Three collections carry the same story; without dedupe a 5-item brief was
    # 4 near-duplicates of one article, which reads as corroboration.
    brief = dedupe_by_content(all_items)[:limit]
    # Positional ToolResult(arg) fills ``content``, not ``structured_content``:
    # the brief was serialised as content and structured_content stayed None,
    # so a client reading the structured field saw nothing.
    return ToolResult(
        content=[
            {
                "type": "text",
                "text": "\n".join(
                    f"[{item['collection']}] {item['content'][:200]}" for item in brief
                ),
            }
        ],
        structured_content={"brief": brief, "query": query, "limit": limit},
    )


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

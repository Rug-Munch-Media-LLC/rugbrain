#!/usr/bin/env python3
"""
rugbrain-mcp — FastMCP facade over RugBrain HTTP API (127.0.0.1:8012).

Routing rule (internal):
  code/protocol/exploit-pattern → context-crypto
  news/scam/wallet/incident/sentiment → rugbrain-mcp
  chain state → rugmunch-data

Does not deprecate context-crypto's query_rugbrain yet — plan:
  1. rugbrain-mcp ships with rugbrain_* tool prefix (this build)
  2. 2-week shadow period: agents use rugbrain_* tools, compare quality
  3. After validation: flip default routing in guide_lookup, mark query_rugbrain deprecated
  4. Remove query_rugbrain from context-crypto in a follow-up PR
"""

from __future__ import annotations

import json
import logging
import sys
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

# ── Config ────────────────────────────────────────────────────────────────
BASE = os.getenv("RUGBRAIN_URL", "http://127.0.0.1:8012").rstrip("/")
TIMEOUT = int(os.getenv("RUGBRAIN_TIMEOUT", "90"))


def _api_key() -> str:
    """Resolve the API key: env first, then the spooled secrets file.

    The MCP server is launched as a bare stdio command, so nothing upstream
    exports the key for us — refusing to start made the facade unreachable
    even though the credential exists on disk. Never logged, never printed.
    """
    key = os.getenv("RMI_RAG_API_KEY", "")
    if key:
        return key
    try:
        for line in Path("/srv/secrets/rugbrain.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("RMI_RAG_API_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    raise RuntimeError("RMI_RAG_API_KEY not found in env or /srv/secrets/rugbrain.env")


API_KEY = _api_key()

# ── Logging ───────────────────────────────────────────────────────────────
# Standard stdio-MCP requirement: JSON-RPC owns STDOUT, so any log line
# written there corrupts the protocol and the client fails the handshake.
# Route logs to STDERR explicitly.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("rugbrain-mcp")

# ── FastMCP server ────────────────────────────────────────────────────────
# ── Market-specialist surface plumbing (moved in 2026-10-12) ──────────────
RAG_API_HOST = os.getenv("RAG_API_HOST", "127.0.0.1")
RAG_API_PORT = int(os.getenv("RAG_API_PORT", "8012"))
BASE_URL = f"http://{RAG_API_HOST}:{RAG_API_PORT}"
HEADERS = {"Content-Type": "application/json"}


class MultiSearchRequest(BaseModel):
    """Body for POST /api/v1/search/multi."""

    query: str = ""
    collections: list[str] = Field(default_factory=lambda: ["news_v2"])
    limit: int = 10
    limit_per_collection: int = 20
    rerank: bool = True


mcp = FastMCP("rugbrain-mcp")


# ── HTTP helper ───────────────────────────────────────────────────────────
def _call(
    path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call RugBrain API. Raises on failure — no silent empty returns."""
    url = f"{BASE}{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v not in (None, "")}
        if filtered:
            url = f"{url}?{urllib.parse.urlencode(filtered)}"

    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "X-API-Key": API_KEY}
    if data is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 — internal RugBrain URL
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 — internal RugBrain URL
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode(errors="replace")
        log.error("rugbrain_http_error", extra={"code": exc.code, "path": path, "detail": detail})
        raise RuntimeError(f"RugBrain HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        log.error("rugbrain_unreachable", extra={"path": path, "err": str(exc)[:200]})
        raise RuntimeError(f"RugBrain unreachable: {exc}") from exc


# ── Tools ─────────────────────────────────────────────────────────────────
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
    """Entity investigation for an address: fund-flow hops, related wallets.

    Args:
        address: Wallet/contract address to investigate.
        max_hops: Max graph traversal depth (1-5, default 2).
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
async def _call_search_multi(
    query: str,
    collections: list[str],
    limit: int = 10,
    limit_per_collection: int = 20,
    rerank: bool = False,
) -> dict[str, Any]:
    """Call the RugBrain /api/v1/search/multi endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/search/multi",
            json=MultiSearchRequest(
                query=query,
                collections=collections,
                limit=limit,
                limit_per_collection=limit_per_collection,
                rerank=rerank,
            ).model_dump(),
            headers=HEADERS,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()


def _extract_results(
    resp: dict[str, Any], collection: str
) -> list[dict[str, Any]]:
    """Normalize raw API search results into a uniform item shape."""
    return [
        {
            "content": item.get("content", ""),
            "collection": collection,
            "score": float(item.get("score", 0.0)),
        }
        for item in resp.get("results", [])
    ]


def _source_of(item: dict[str, Any]) -> str:
    """The collection an item came from.

    The API tags fused hits with ``_source_collections`` (a list, because RRF
    can fuse the same doc from several); the older shape used ``collection``.
    """
    sources = item.get("_source_collections")
    if isinstance(sources, list) and sources:
        return str(sources[0])
    single = item.get("collection")
    return str(single) if isinstance(single, str) else "?"


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
                    f"[{_source_of(item)}] "
                    f"{item.get('content', '')[:300]}"
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
        default=10, ge=1, le=100,
        description="Maximum number of items to return.",
    ),
) -> ToolResult:
    """What's happening in crypto right now.

    Fuses news_v2, social_intel, and education collections, reranked,
    each item carrying its source collection and relevance score.
    """
    collections = ["news_v2", "social_intel", "education"]
    all_items: list[dict[str, Any]] = []
    for coll in collections:
        resp = await _call_search_multi(
            query=query,
            collections=[coll],
            limit=limit,
            limit_per_collection=limit,
            rerank=True,
        )
        all_items.extend(_extract_results(resp, coll))
    all_items.sort(key=lambda x: x["score"], reverse=True)
    return ToolResult(
        {"brief": all_items[:limit], "query": query, "limit": limit}
    )


@mcp.tool()
async def rugbrain_scam_radar(
    query: str = Field(
        description="Token, address, or topic (e.g. '0x123...', 'BONK', 'pump and dump').",
    ),
    limit: int = Field(
        default=10, ge=1, le=100,
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
    return ToolResult(
        {"results": all_items[:limit], "query": query, "limit": limit}
    )


def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    mcp.run()

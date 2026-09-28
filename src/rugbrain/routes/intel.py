"""Intelligence routes: journalism research, evaluation, email accounts, news
rating/top articles, fear index, and market context. Split out of
``rugbrain/endpoints.py`` (phase 3, 2026-08-21); no URL prefix — the parent
router in ``endpoints.py`` supplies ``/api/v1``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from rugbrain.endpoints import _is_token_address

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["rag-optimization"])


@router.get("/rag/journalism/research")
async def journalism_research(
    request: Request,
    topic: str = Query(
        default="", description="Research topic — e.g. 'cross-chain bridge hacks 2024'"
    ),
    limit: int = Query(default=15, ge=1, le=50, description="Max docs per collection"),
) -> dict[str, Any]:
    """
    Deep-dive research for crypto journalism.

    Queries all three RAG collections (``threat_intel``, ``rmi_agent_memory``,
    ``rmi_community``) and returns a structured research brief with:

    - **Timeline**: Chronological events extracted from threat intel
    - **Key Entities**: Threat actors, protocols, vulnerability patterns
    - **Related Scams**: Matching scam/hack records with severity classification
    - **Community Insights**: Best practices and contextual knowledge
    - **Cross-Collection Connections**: Entities linked across threat and memory data
    - **Synthesis**: Markdown summary with quality metrics

    Designed for:
    - **Journalists**: Research briefs for articles and investigations
    - **Bots**: Structured data for automated content generation
    - **Scanners**: Context enrichment during threat analysis
    - **Historical analysis**: Trend identification across events

    Example::

        curl "http://localhost:8012/api/v1/rag/journalism/research?topic=Solana+bridge+security"
    """
    from rugbrain.journalism import research_topic

    if not topic:
        return {
            "topic": "",
            "error": "topic query parameter is required",
            "status": "error",
            "research_timestamp": datetime.now(UTC).isoformat(),
        }

    try:
        brief = await research_topic(topic, max_docs=limit)
        brief["status"] = (
            "ok"
            if brief.get("total_docs_retrieved", {}).get("threat_intel_v2", 0) > 0
            else "low_relevance"
        )
        return brief
    except Exception as e:
        logger.exception("journalism_research_failed topic=%s", topic[:60])
        return {
            "topic": topic,
            "error": str(e)[:300],
            "status": "error",
            "research_timestamp": datetime.now(UTC).isoformat(),
        }


@router.get("/eval/run")
async def eval_run(
    request: Request,
    limit: int = 0,
    skip_answers: bool = False,
) -> dict[str, Any]:
    """Run the RAGAS-style golden-set evaluation against the live search endpoint.

    Executes ``run_evaluation()`` from ``rugbrain.evaluation``: every query in
    the golden test set is sent through ``/api/v1/search`` and scored for
    context precision/recall. Returns the aggregate pass/fail report plus
    per-query results.

    Queries run concurrently and each is bounded by a 10s timeout, so the
    full run completes in seconds-to-a-minute instead of 30+ minutes.

    Example::

        curl "http://localhost:8012/api/v1/eval/run"
        curl "http://localhost:8012/api/v1/eval/run?limit=3"         # smoke
        curl "http://localhost:8012/api/v1/eval/run?skip_answers=1"  # retrieval-only
    """
    from rugbrain.evaluation import run_evaluation

    try:
        return await run_evaluation(
            limit=limit or None,
            skip_answers=skip_answers,
        )
    except Exception as exc:
        logger.warning("eval_run_failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "evaluation_failed", "message": str(exc)[:200]},
        ) from exc


@router.get("/email/accounts")
async def email_accounts(request: Request) -> dict[str, Any]:
    """List configured mail accounts.

    The account list comes from the ``RMI_EMAIL_ACCOUNTS`` env var — a
    comma-separated list of ``id|email|type`` entries — so no personal
    addresses are hardcoded in the repo. Returns an empty list when
    unconfigured.
    """
    accounts: list[dict[str, str]] = []
    for entry in os.getenv("RMI_EMAIL_ACCOUNTS", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) == 2:
            account_id, email = parts
            account_type = "gmail"
        elif len(parts) == 3:
            account_id, email, account_type = parts
        else:
            logger.warning("email_accounts: skipping malformed entry %r", entry[:40])
            continue
        accounts.append({"id": account_id, "email": email, "type": account_type})

    return {
        "accounts": accounts,
        "webmail_url": os.getenv("RMI_WEBMAIL_URL", ""),
    }


# ═══════════════════════════════════════════════════════════════════════
# News rating + crypto fear index (RugBrain news ranking)
# ═══════════════════════════════════════════════════════════════════════

NEWS_TOP_MAX_SCROLL_PAGES = 10


@router.get("/news/rate")
async def news_rate_article(
    request: Request,
    url: str = Query(
        default=...,
        description=(
            "Article URL — used for source-reputation scoring. Never fetched "
            "server-side (SSRF-safe); rate only the provided metadata."
        ),
    ),
    source: str | None = Query(default=None, description="Source name (optional, e.g. 'CoinDesk')"),
    title: str | None = Query(default=None, description="Article title"),
    content: str | None = Query(
        default=None, description="Article body text — recommended for a full rating"
    ),
    published_at: str | None = Query(
        default=None, description="ISO-8601 publish timestamp (e.g. 2026-08-04T09:00:00Z)"
    ),
    corroborating_sources: int | None = Query(
        default=None, ge=0, le=100, description="Independent sources reporting the same event"
    ),
    source_reputation_override: int | None = Query(
        default=None, ge=0, le=100, description="Explicit 0-100 source reputation score"
    ),
    aggregated: bool = Query(
        default=False, description="True when the article aggregates multiple sources"
    ),
) -> dict[str, Any]:
    """Rate a news article on seven quality axes (Ground News-style).

    Computes a 0-100 score per axis (source credibility, factual density,
    timeliness, bias, corroboration, technical depth, source diversity)
    plus a weighted 0-100 composite and a tier. The URL is used only for
    source reputation lookup — it is **never fetched server-side** (SSRF
    safety). Pass the article body via ``content`` for a full rating::

        curl "http://localhost:8012/api/v1/news/rate?url=https://coindesk.com/...&content=The%20protocol%20lost%20%2412m..."
    """
    from rugbrain.news_rating import rate_article

    rating = rate_article(
        {
            "url": url,
            "source": source or "",
            "title": title or "",
            "content": content or "",
            "published_at": published_at,
            "corroborating_sources": corroborating_sources,
            "source_reputation_override": source_reputation_override,
            "aggregated": aggregated,
        }
    )
    return {
        "url": rating.url,
        "source": rating.source,
        "axes": rating.axes,
        "composite": rating.composite,
        "tier": rating.tier,
        "factuality": rating.factuality,
        "source_reputation": rating.source_reputation,
        "corroborating_sources": rating.corroborating_sources,
        "on_chain_evidence": rating.on_chain_evidence,
        "signals": rating.signals,
        "rated_at": datetime.now(UTC).isoformat(),
        "note": (
            "rating computed from provided metadata only; the URL is never fetched server-side"
        ),
    }


@router.get("/news/top")
async def news_top_articles(
    request: Request,
    category: str | None = Query(
        default=None,
        description="Filter by category (e.g. security, defi, ethereum, solana)",
    ),
    min_quality: int = Query(
        default=70, ge=0, le=100, description="Minimum composite quality 0-100"
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Max articles to return"),
) -> dict[str, Any]:
    """Return the best articles from the Qdrant `news_v2` collection.

    Scrolls the collection (payload has no vector score) and ranks by the best
    quality signal a point actually carries:

    1. ``metadata.rating`` — the news_rating composite, when the rating engine
       has run for that point.
    2. ``metadata.importance`` — the Telegram ingester's own importance score
       (keyword weight x category weight, amplified by cross-channel echo).

    (2) exists because nothing runs the rating engine on the Telegram path, so
    requiring ``metadata.rating`` made this endpoint return an empty list for
    every request while looking healthy. The response reports which signal was
    used per article, so a reader can tell rated content from heuristic-ranked
    content. Fails open to an empty list when Qdrant is unreachable.
    """
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore()
    collected: list[dict[str, Any]] = []
    try:
        offset: str | None = None
        page = 0
        while page < NEWS_TOP_MAX_SCROLL_PAGES and len(collected) < limit * 4:
            results, offset = await store.scroll("news_v2", limit=100, offset=offset)
            if not results:
                break
            collected.extend(results)
            page += 1
            if not offset:
                break
    except Exception as exc:
        logger.warning("news_top_scroll_failed: %s", exc)
        return {
            "articles": [],
            "total": 0,
            "category": category,
            "min_quality": min_quality,
            "collection": "news_v2",
            "error": str(exc)[:200],
        }

    from rugbrain.news_selection import select_articles

    articles = select_articles(collected, category=category, min_quality=min_quality)
    return {
        "articles": articles[:limit],
        "total": len(articles),
        "category": category,
        "min_quality": min_quality,
        "collection": "news_v2",
    }


@router.get("/fear-index")
async def fear_index_current(
    request: Request,
    window: str = Query(
        default="24h",
        pattern="^(1h|6h|24h|7d)$",
        description="Sample window for the returned trend (1h|6h|24h|7d)",
    ),
) -> dict[str, Any]:
    """Current crypto panic/fear index (0 = extreme fear, 100 = extreme greed).

    Blends Alternative.me Fear & Greed, SearXNG social sentiment, on-chain
    exchange/whale/liquidation signals and aggregated news sentiment. The
    snapshot is polled every 5 minutes, cached in Redis, and persisted to
    ClickHouse. Fails open to a neutral 50 when no provider is reachable::

        curl "http://localhost:8012/api/v1/fear-index?window=24h"
    """
    from rugbrain.fear_index import get_fear_index

    try:
        return await get_fear_index(window=window)
    except Exception as exc:
        logger.warning("fear_index_endpoint_failed: %s", exc)
        return {
            "value": 50,
            "classification": "neutral",
            "components": {},
            "timestamp": datetime.now(UTC).isoformat(),
            "window": window,
            "samples": [],
            "source": "error",
            "error": str(exc)[:200],
        }


@router.get("/fear-index/history")
async def fear_index_history_endpoint(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Number of days of history"),
) -> dict[str, Any]:
    """Historical fear-index chart data (ClickHouse-first, memory fallback).

    Returns ``{"days", "points": [{"ts", "value", "classification"}],
    "source"}`` suitable for charting::

        curl "http://localhost:8012/api/v1/fear-index/history?days=30"
    """
    from rugbrain.fear_index import fear_index_history

    try:
        return await fear_index_history(days=days)
    except Exception as exc:
        logger.warning("fear_index_history_failed: %s", exc)
        return {
            "days": days,
            "points": [],
            "source": "error",
            "error": str(exc)[:200],
        }


@router.get("/market/mood")
async def market_mood_endpoint(
    request: Request,
    chain: str = Query(default="ethereum", description="Chain to scope chain activity"),
) -> dict[str, Any]:
    """Global market mood digest (fear index + chain activity + recent exploits).

    Called by the scanner before each scan (Part 1 — market-aware scanning)::

        curl "http://localhost:8012/api/v1/market/mood?chain=ethereum"

    Returns ``fear_index``, ``market_sentiment``, ``recent_exploits_24h``,
    ``chain_volume_24h`` (TVL change) and ``chain_activity``. Fails open to
    neutral values when providers are unreachable.
    """
    from rugbrain.market_context import build_market_mood

    try:
        return await build_market_mood(chain=chain)
    except Exception as exc:
        logger.warning("market_mood_failed: %s", exc)
        return {
            "fear_index": 50.0,
            "fear_classification": "neutral",
            "market_sentiment": "neutral",
            "recent_exploits_24h": 0,
            "chain_volume_24h": 0.0,
            "chain_activity": {
                "chain": chain,
                "tvl": 0.0,
                "tvl_change_24h": 0.0,
                "new_deploys_24h": 0,
            },
            "generated_at": datetime.now(UTC).isoformat(),
            "error": str(exc)[:200],
        }


@router.get("/market/context/{address}")
async def market_context_token_endpoint(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum", description="Chain of the token/contract"),
) -> dict[str, Any]:
    """Full market context for a token (Part 3 — called by the scanner).

    Aggregates fear index, chain activity (24h volume + new deploys),
    social signals (mentions + sentiment), recent exploits in the same
    category/chain, and the performance of historically similar tokens::

        curl "http://localhost:8012/api/v1/market/context/0xabc...?chain=ethereum"

    Fails open: every provider degrades to neutral values so the scanner
    always gets a well-formed context object.
    """
    if not _is_token_address(address):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_address",
                "message": "address must be a 0x-prefixed 40-hex token/contract address",
            },
        )

    from rugbrain.market_context import build_token_market_context

    try:
        return await build_token_market_context(address=address, chain=chain)
    except Exception as exc:
        logger.warning("market_context_failed: %s", exc)
        return {
            "address": address,
            "chain": chain,
            "fear_index": 50.0,
            "fear_classification": "neutral",
            "market_sentiment": "neutral",
            "chain_activity": {
                "chain": chain,
                "tvl": 0.0,
                "tvl_change_24h": 0.0,
                "new_deploys_24h": 0,
            },
            "chain_volume_24h": 0.0,
            "recent_exploits": [],
            "social_signals": {
                "sentiment": "unknown",
                "confidence": 0.0,
                "mentions": 0,
                "mention_velocity": 0.0,
            },
            "deployer_freshness": "unknown",
            "similar_tokens": [],
            "generated_at": datetime.now(UTC).isoformat(),
            "error": str(exc)[:200],
        }

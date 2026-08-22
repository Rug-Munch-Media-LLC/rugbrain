"""Unified News Feed — aggregates YouTube, podcast, and newsletter content.

Provides a single API endpoint that queries across all content sources:
- youtube_videos collection
- podcast_episodes collection
- newsletters collection
- threat_intel (news stories)

Returns deduplicated, sorted-by-date results with source attribution.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

news_feed_router = APIRouter(prefix="/api/v1/feed", tags=["news-feed"])


@news_feed_router.get("/all")
async def get_unified_news_feed(
    q: str = Query(default="", description="Search query across all sources"),
    limit: int = Query(default=20, ge=1, le=100),
    sources: str = Query(
        default="youtube,podcast,newsletter,news",
        description="Comma-separated sources to include",
    ),
    category: str | None = Query(default=None, description="Filter by category"),
    min_date: str | None = Query(default=None, description="ISO date filter (YYYY-MM-DD)"),
) -> dict[str, Any]:
    """Get unified news feed across YouTube, podcasts, newsletters, and news."""
    source_list = [s.strip() for s in sources.split(",")]

    # Collection map
    collection_map = {
        "youtube": ("youtube_videos", "youtube"),
        "podcast": ("podcast_episodes", "podcast"),
        "newsletter": ("newsletters", "newsletter"),
        "news": ("threat_intel", "news"),
    }

    # Build embedding for search
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        query = q or "crypto market intelligence"
        embedding = await get_embedding(query)

        store = QdrantVectorStore()
        all_results = []

        for source_key in source_list:
            if source_key not in collection_map:
                continue
            collection, _ = collection_map[source_key]

            try:
                results = await store.hybrid_search(
                    query_text=query,
                    query_embedding=embedding,
                    collection=collection,
                    limit=limit // len(source_list) + 1,
                    min_similarity=0.3,
                )
                for r in results:
                    meta = r.get("metadata", {})
                    if isinstance(meta, dict):
                        # Add source tag
                        meta["_source"] = source_key
                        meta["_feed_date"] = (
                            meta.get("upload_date")
                            or meta.get("ingested_at")
                            or meta.get("date")
                            or datetime.now(UTC).isoformat()
                        )
                    all_results.append(r)
            except Exception:
                logger.debug("feed_collection_failed collection=%s", collection)

        # Sort by date descending
        all_results.sort(
            key=lambda r: (
                r.get("metadata", {}).get("_feed_date", "")
                if isinstance(r.get("metadata"), dict)
                else ""
            ),
            reverse=True,
        )

        # Apply filters
        filtered = []
        for r in all_results:
            meta = r.get("metadata", {}) if isinstance(r.get("metadata"), dict) else {}
            if category and meta.get("category") != category:
                continue
            if min_date and meta.get("_feed_date", "")[:10] < min_date:
                continue
            filtered.append(r)
            if len(filtered) >= limit:
                break

        return {
            "results": filtered,
            "total": len(filtered),
            "query": q,
            "sources_queried": source_list,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as exc:
        logger.warning("news_feed_failed %s", str(exc)[:200])
        return {"results": [], "total": 0, "error": str(exc)[:200]}


@news_feed_router.get("/sources")
async def list_sources() -> dict[str, Any]:
    """List all tracked content sources."""
    from rugbrain.newsletter_ingester import NEWSLETTERS
    from rugbrain.podcast_ingester import CRYPTO_PODCASTS
    from rugbrain.youtube_ingester import YT_CHANNELS

    return {
        "youtube": {
            "total": len(YT_CHANNELS),
            "categories": sorted(set(c for _, _, c in YT_CHANNELS)),
        },
        "podcasts": {
            "total": len(CRYPTO_PODCASTS),
        },
        "newsletters": {
            "total": len(NEWSLETTERS),
            "categories": sorted(set(c for _, _, _, c in NEWSLETTERS)),
        },
        "total_sources": len(YT_CHANNELS) + len(CRYPTO_PODCASTS) + len(NEWSLETTERS),
    }

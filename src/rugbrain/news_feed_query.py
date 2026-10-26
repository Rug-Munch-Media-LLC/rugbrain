"""Fetch and select the rows behind the unified news feed.

One job: given the requested sources, return publishable, deduplicated rows.
``news_feed`` keeps only the HTTP parameter surface, which is why the route
function stays short.

Served collections only (2026-10-27). "threat_intel" does not exist and
"youtube_videos" was never created, so two of the four sources the route used
to advertise returned nothing for every query; the live news corpus is
``news_articles_8b``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

from rugbrain.result_quality import dedupe_by_content, is_publishable_news

logger = logging.getLogger(__name__)

#: Source key → the collection that actually holds it.
SOURCE_COLLECTIONS: Final[dict[str, str]] = {
    "podcast": "podcast_episodes",
    "newsletter": "newsletters",
    "news": "news_articles_8b",
}

#: An empty query is a real query for a general feed, not "no query".
DEFAULT_QUERY: Final = "crypto market intelligence"


async def fetch_feed_rows(query_text: str, source_list: list[str], per_source: int) -> list[dict[str, Any]]:
    """Hybrid-search each selected source and tag each row with its source."""
    from rugbrain.embeddings import get_embedding
    from rugbrain.qdrant_store import QdrantVectorStore

    embedding = await get_embedding(query_text)
    store = QdrantVectorStore()
    rows: list[dict[str, Any]] = []
    for source_key in source_list:
        collection = SOURCE_COLLECTIONS.get(source_key)
        if collection is None:
            logger.debug("feed_source_unknown source=%s", source_key)
            continue
        try:
            found = await store.hybrid_search(
                query_text=query_text,
                query_embedding=embedding,
                collection=collection,
                limit=per_source,
                min_similarity=0.3,
            )
        except Exception:
            logger.debug("feed_collection_failed collection=%s", collection)
            continue
        for row in found:
            meta = row.get("metadata")
            if isinstance(meta, dict):
                meta["_source"] = source_key
                meta["_feed_date"] = (
                    meta.get("upload_date")
                    or meta.get("ingested_at")
                    or meta.get("date")
                    or datetime.now(UTC).isoformat()
                )
            rows.append(row)
    return rows


def select_feed_rows(
    rows: list[dict[str, Any]],
    category: str | None,
    min_date: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Quality-gate, filter, sort newest-first, then collapse repeats.

    The collections hold raw crawls as well as articles, so a generic query
    ranked page chrome (nav menus, cookie-consent CSS) above real content.
    """
    selected: list[dict[str, Any]] = []
    for row in rows:
        meta = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        if category and meta.get("category") != category:
            continue
        if min_date and str(meta.get("_feed_date", ""))[:10] < min_date:
            continue
        if not is_publishable_news(row):
            continue
        selected.append(row)
    selected.sort(
        key=lambda r: str((r.get("metadata") or {}).get("_feed_date", "")),
        reverse=True,
    )
    return dedupe_by_content(selected)[:limit]


__all__ = ["DEFAULT_QUERY", "SOURCE_COLLECTIONS", "fetch_feed_rows", "select_feed_rows"]

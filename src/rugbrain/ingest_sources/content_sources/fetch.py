"""One bounded, conditional fetch per catalogue source.

Every source is a single request: RSS via the shared conditional-GET transport
(:mod:`rugbrain.rss_feed`, which replays ETag/Last-Modified and treats 304 as
"unchanged") and YouTube via its channel Atom feed. Failures are data — the
record says what happened and the batch continues. No pagination, no crawling,
no retries beyond the transport's own timeout.
"""

from __future__ import annotations

from typing import Any

import httpx

from rugbrain.ingest_sources.content_sources.spec import SourceSpec
from rugbrain.ingest_sources.content_sources.youtube_feed import parse_channel_feed
from rugbrain.rss_feed import fetch_feed

__all__ = ["FETCH_TIMEOUT", "USER_AGENT", "fetch_source"]

FETCH_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
USER_AGENT = "RugMunch-RAG/2.0 (+https://rugmunch.io; content-sources)"
_ENTRY_LIMIT = 20


async def fetch_source(
    spec: SourceSpec, *, client: httpx.AsyncClient | None = None, limit: int = _ENTRY_LIMIT
) -> dict[str, Any]:
    """Fetch one source and return ``{source_id, http_status, entries, error}``."""
    shared = client is not None
    session = client or httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        if spec.kind == "youtube":
            resp = await session.get(spec.feed_url)
            entries = parse_channel_feed(resp.text, spec)[:limit] if resp.status_code == 200 else []
            return {
                "source_id": spec.id,
                "http_status": resp.status_code,
                "entries": entries,
                "error": None if entries else "no entries parsed",
            }
        record = await fetch_feed(session, spec.feed_url)
        return {
            "source_id": spec.id,
            "http_status": record["http_status"],
            "entries": list(record["items"])[:limit],
            "error": record["error"],
        }
    finally:
        if not shared:
            await session.aclose()

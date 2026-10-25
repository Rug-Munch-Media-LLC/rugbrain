"""Map any source entry onto the news channel's canonical item shape.

"Parity" here is literal: an item produced from a Substack, Medium, paragraph or
YouTube entry must be byte-compatible with what ``firehose.direct_sources``
emits for an RSS article — same collection, same ``News: {title}. {summary}``
content prefix, same metadata keys, and ``published`` in RFC 2822 — so the
existing chunker, embedder, dedup, rerank and store need no parallel path.

``published`` is normalised because ``news_articles`` stores RFC 2822 (see
``content_story``): YouTube hands back ISO-8601, Medium hands back RFC 2822.
"""

from __future__ import annotations

import logging
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any

from rugbrain.ingest_sources.content_sources.sentiment import analyze_item
from rugbrain.ingest_sources.content_sources.spec import SourceSpec

logger = logging.getLogger(__name__)

__all__ = ["NEWS_COLLECTION", "to_news_item"]

NEWS_COLLECTION = "news_articles"
_CONTENT_CAP = 2000
_TRANSCRIPT_CAP = 4000


def _rfc822(value: str) -> str:
    """Best-effort RFC 2822 stamp; unparseable input is passed through."""
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return format_datetime(parsedate_to_datetime(raw), usegmt=True)
    except (TypeError, ValueError) as exc:
        logger.debug("suppressed %s: %s", type(exc).__name__, exc)
    try:
        return format_datetime(datetime.fromisoformat(raw.replace("Z", "+00:00")), usegmt=True)
    except ValueError:
        return raw


def _first(entry: dict[str, Any], *keys: str) -> str:
    """First non-empty value among ``keys`` — feeds disagree on field names."""
    for key in keys:
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return ""


def _joined(chunks: list[dict[str, Any]] | None) -> str:
    """One searchable string from timestamped chunks (timestamps stay in metadata)."""
    return " ".join(str(chunk.get("text") or "") for chunk in chunks or []).strip()


def to_news_item(
    spec: SourceSpec,
    entry: dict[str, Any],
    *,
    transcript_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One catalogue entry → one ``news_articles`` document with provenance."""
    title = _first(entry, "title")
    summary = _first(entry, "summary", "description")
    analysis = analyze_item(summary, title=title)
    content = f"News: {title}. {summary}"[:_CONTENT_CAP]
    metadata: dict[str, Any] = {
        "source": spec.id,
        "publisher": spec.publisher,
        "url": _first(entry, "url", "link"),
        "published": _rfc822(_first(entry, "published_at", "date")),
        "category": spec.category,
        "title": title,
        "kind": spec.kind,
        "provenance": spec.provenance(),
        "sentiment": analysis["sentiment"],
        "tickers": analysis["tickers"],
        "entities": analysis["entities"],
    }
    transcript = _joined(transcript_chunks)
    if transcript:
        content = f"{content} Transcript: {transcript}"[:_TRANSCRIPT_CAP]
        metadata["transcript_chunks"] = transcript_chunks
        metadata["transcript_status"] = str(entry.get("transcript_status") or "captions:unknown")
    return {"collection": NEWS_COLLECTION, "content": content, "metadata": metadata}

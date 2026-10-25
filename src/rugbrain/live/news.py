"""news_now — rugbrain news corpus plus a live search, every hit dated and staleness-flagged."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from .envelope import (
    NEWS_STALE_AFTER_S,
    Envelope,
    age_seconds,
    coerce_ts,
    iso,
    ok_envelope,
    refuse,
    utcnow,
)
from .fetch import ProviderError
from .search import web_search

CorpusFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]

_DATE_KEYS = ("published_at", "publishedDate", "date", "upload_date", "ingested_at")


def _first_date(meta: dict[str, Any]) -> str | None:
    for key in _DATE_KEYS:
        parsed = coerce_ts(meta.get(key))
        if parsed is not None:
            return iso(parsed)
    return None


async def _corpus_search(topic: str, n: int) -> list[dict[str, Any]]:
    """Query the checked-in news corpus (Qdrant threat_intel). Fails typed, never silently."""
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(topic)
        rows = await QdrantVectorStore().hybrid_search(
            query_text=topic,
            query_embedding=embedding,
            collection=os.getenv("LIVE_NEWS_COLLECTION", "threat_intel"),
            limit=n,
            min_similarity=0.3,
        )
    except Exception as exc:  # noqa: BLE001 - typed refusal downstream
        raise ProviderError("rugbrain-corpus", "corpus_unavailable", type(exc).__name__) from exc
    items = []
    for row in rows or []:
        meta = row.get("metadata") if isinstance(row, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        items.append(
            {
                "title": (meta.get("title") or row.get("title") or "").strip(),
                "url": (meta.get("url") or "").strip(),
                "snippet": (meta.get("summary") or meta.get("summary_short") or row.get("text") or "")[:400],
                "published_at": _first_date(meta),
                "source": "corpus",
            }
        )
    return items


def _flag(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        age = age_seconds(item.get("published_at"))
        item["stale"] = bool(age is not None and age > NEWS_STALE_AFTER_S)
        item["dated"] = item.get("published_at") is not None
    return items


async def news_now(topic: str, n: int = 10, *, corpus_fn: CorpusFn | None = None) -> Envelope:
    """Corpus hits (dated) plus live search hits. >24 h old is flagged stale, per item."""
    topic = (topic or "").strip()
    if not topic:
        return refuse("bad_request", "topic is required", "news_now")
    errors: list[str] = []
    corpus: list[dict[str, Any]] = []
    try:
        corpus = await (corpus_fn or _corpus_search)(topic, n)
    except ProviderError as exc:
        errors.append(f"{exc.provider}:{exc.code}")

    live = await web_search(topic, n)
    web: list[dict[str, Any]] = list(live.data or []) if live.ok else []
    if not live.ok and live.error:
        errors.append(f"{live.error['code']}:{live.error['reason']}"[:160])

    items = _flag(corpus + web)
    items.sort(key=lambda it: it.get("published_at") or "", reverse=True)
    items = items[: max(1, n)]
    if not items:
        return refuse("all_sources_empty", "; ".join(errors) or "no corpus or web hits", "news_now")
    return ok_envelope(
        "corpus+web",
        items,
        iso(utcnow()),
        stale=all(item["stale"] for item in items),
    )

"""Qdrant result → normalised story dict for the content engine.

Split out of :mod:`rugbrain.content_engine` (2026-09-28) so the headline
styling change did not grow an already-oversized allowlisted module. Pure
functions over plain dicts — no I/O, no clients.

The ``id``, ``category`` and ``ts`` fields are load-bearing for headline
styling: ``id`` keys the deterministic emoji rotation (see
:mod:`rugbrain.formatting.emoji`) and ``ts`` feeds the bot's recency gate.
"""

from __future__ import annotations

from typing import Any

#: Sort rank per severity — lower sorts earlier within equal importance.
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

#: Field defaults for a Qdrant news_v2 result.
_FIELDS: dict[str, Any] = {
    "title": "",
    "severity": "medium",
    "category": "",
    "source": "",
    "url": "",
    "importance": 0.0,
    "kind": "story",
    "ts": None,
}


def normalise(result: dict[str, Any], collection: str) -> dict[str, Any]:
    """Flatten one Qdrant search result into a story dict.

    ``category`` falls back to ``content_type`` because the sentiment/tweet
    datasets label their rows that way, and an empty category made a 298k-point
    family look like one unnamed bucket.
    """
    md = result.get("metadata") or {}
    content = result.get("content") or md.get("text") or md.get("title", "")
    return {
        "id": result.get("id", ""),
        "title": md.get("title", _FIELDS["title"]),
        "content": content,
        "severity": (md.get("severity") or _FIELDS["severity"]).lower(),
        "category": (md.get("category") or md.get("content_type") or md.get("kind") or "").lower(),
        "source": result.get("source", ""),
        "url": md.get("url", ""),
        "importance": float(md.get("importance") or 0.0),
        "kind": md.get("kind", _FIELDS["kind"]),
        "collection": collection,
        "ts": md.get("ts") or result.get("ts"),
        "tweet_id": md.get("tweet_id") or "",
    }


def dedupe_key(result: dict[str, Any]) -> str:
    """Content fingerprint used to collapse near-duplicate hits.

    A known tweet id IS the identity: the same post appears with and without an
    author (``x.com/i/status/…`` vs ``x.com/handle/status/…``), so keying on the
    body prefix let both copies through. Fall back to the body when no id.
    """
    md = result.get("metadata") or {}
    tweet_id = str(md.get("tweet_id") or "").strip()
    if tweet_id:
        return f"tweet:{tweet_id}"
    return (result.get("content") or "")[:120]


def rank(stories: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Importance desc, then severity, then keep insertion order."""
    return sorted(
        stories.values(),
        key=lambda s: (-s["importance"], SEVERITY_RANK.get(s["severity"], 2)),
    )


def article_headline(result: dict[str, Any]) -> str:
    """Display headline for a ``news_v2`` article (the site news section).

    Plain text by construction — this JSON is rendered by the site's news
    list, so a ``<tg-emoji>`` tag would surface as literal text on the page.
    """
    from rugbrain.formatting.headline import style_for_site

    md = result.get("metadata") or {}
    return style_for_site(
        {
            "id": result.get("id"),
            "title": md.get("title", ""),
            "category": md.get("category", ""),
            "severity": md.get("severity", ""),
            "url": md.get("url", ""),
        }
    )

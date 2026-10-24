"""Qdrant result → normalised story dict for the content engine.

Split out of :mod:`rugbrain.content_engine` (2026-09-28) so the headline
styling change did not grow an already-oversized allowlisted module. Pure
functions over plain dicts — no I/O, no clients.

The ``id``, ``category`` and ``ts`` fields are load-bearing for headline
styling: ``id`` keys the deterministic emoji rotation (see
:mod:`rugbrain.formatting.emoji`) and ``ts`` feeds the bot's recency gate.

Every source family writes a slightly different payload shape, and the
normalisation stage is where they become one shape:

===============  ==========================  ==============================
collection       timestamp field             link field
===============  ==========================  ==============================
news_v2          ``ts`` (ISO, or tweet fmt)  ``url``
social_intel     ``ts`` (ISO)                ``url``
research_v2      ``published`` (ISO)         ``source_url``
news_articles    ``published`` (RFC 2822)    ``url``
===============  ==========================  ==============================
"""

from __future__ import annotations

from typing import Any

from rugbrain.content_time import to_iso8601

#: Sort rank per severity — lower sorts earlier within equal importance.
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

#: Field defaults for a normalised story.
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

#: Title used when the payload carries none — RSS research docs open with the
#: headline as their first line, and an empty headline renders an empty post.
TITLE_CHARS = 140


def _title(md: dict[str, Any], content: str) -> str:
    """Payload title, else the first line of the body."""
    title = str(md.get("title") or "").strip()
    if title:
        return title
    first = str(content or "").strip().split("\n", 1)[0].strip().rstrip(".")
    return first[:TITLE_CHARS]


def normalise(result: dict[str, Any], collection: str) -> dict[str, Any]:
    """Flatten one Qdrant search result into a story dict.

    ``category`` falls back to ``content_type`` because the sentiment/tweet
    datasets label their rows that way, and an empty category made a 298k-point
    family look like one unnamed bucket.

    ``ts`` falls back to ``published`` and is normalised to ISO-8601 by
    :func:`rugbrain.content_time.to_iso8601`: the X lane stores the raw tweet
    dialect and the recency gate fails closed on it, so without this stage
    every X post was silently unpublishable.
    """
    md = result.get("metadata") or {}
    content = result.get("content") or md.get("text") or md.get("title", "")
    return {
        "id": result.get("id", ""),
        "title": _title(md, content),
        "content": content,
        "severity": (md.get("severity") or _FIELDS["severity"]).lower(),
        "category": (md.get("category") or md.get("content_type") or md.get("kind") or "").lower(),
        "source": result.get("source", ""),
        "url": md.get("url") or md.get("source_url") or "",
        "importance": float(md.get("importance") or 0.0),
        "kind": md.get("kind", _FIELDS["kind"]),
        "collection": collection,
        "ts": to_iso8601(md.get("ts") or md.get("published") or result.get("ts")),
        "tweet_id": md.get("tweet_id") or "",
    }


def dedupe_key(result: dict[str, Any]) -> str:
    """Content fingerprint used to collapse near-duplicate hits.

    A known tweet id IS the identity: the same post appears with and without an
    author (``x.com/i/status/…`` vs ``x.com/handle/status/…``), so keying on the
    body prefix let both copies through. Fall back to the body when no id.

    This is the CHEAP key, exact by construction. Cross-channel syndication is
    collapsed by :func:`rugbrain.content_dedupe.collapse` after normalisation —
    two channels pasting the same wire story have different bodies here.
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

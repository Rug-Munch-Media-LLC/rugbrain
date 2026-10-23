"""Dedup helpers for the X ingest pipeline (in-batch + cross-run, fail-open).

Two identity keys are used, because one is not enough:

* **tweet id** — the same status fetched twice (rotation, lane failover).
* **text fingerprint** — the same words fetched under a DIFFERENT id or with the
  author missing. Measured 2026-10-24: the PeckShield exploit post was stored
  twice, once as ``x.com/peckshield/status/…`` and once as ``x.com/i/status/…``
  with an empty handle, so the point ids (source+url+hash) differed and both
  survived into the corpus and into selection.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from rugbrain.ingest_sources.x_intel.config import MIN_TWEET_CHARS, RAG_COLLECTION

logger = logging.getLogger("rugbrain.x_intel")

#: Cross-run seen-set TTL. Long enough to cover a weekly re-scrape cycle.
SEEN_TTL_S: int = 7 * 86400


def text_fingerprint(text: str) -> str:
    """Stable 16-hex fingerprint of a post body (whitespace-normalised)."""
    flat = " ".join(str(text or "").split()).lower()
    return hashlib.sha256(flat.encode("utf-8")).hexdigest()[:16]


async def dedupe_seen(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by tweet id + text fingerprint (in-batch) then Redis (cross-run).

    Redis down → everything treated as fresh (fail-open).
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for t in tweets:
        tid = str(t.get("tweet_id") or "")
        if not tid or tid in seen:
            continue
        fingerprint = text_fingerprint(str(t.get("text") or ""))
        if fingerprint and fingerprint in seen_text:
            continue
        seen.add(tid)
        if fingerprint:
            seen_text.add(fingerprint)
        unique.append(t)
    try:
        from rugbrain.compat import get_redis_async

        r = await get_redis_async()
    except Exception as exc:
        logger.debug("xintel:dedupe_redis_unavailable err=%s", str(exc)[:120])
        return unique
    fresh: list[dict[str, Any]] = []
    for t in unique:
        keys = [f"xintel:seen:{t['tweet_id']}"]
        fingerprint = text_fingerprint(str(t.get("text") or ""))
        if fingerprint:
            keys.append(f"xintel:seen_text:{fingerprint}")
        try:
            claimed = await r.set(keys[0], 1, nx=True, ex=SEEN_TTL_S)
            if claimed and len(keys) > 1:
                await r.set(keys[1], 1, nx=False, ex=SEEN_TTL_S)
        except Exception as exc:
            logger.debug("xintel:dedupe_set_failed err=%s", str(exc)[:120])
            claimed = True
        if claimed:
            fresh.append(t)
    return fresh


def _content_line(tweet: dict[str, Any], text: str, handle: str) -> str:
    """The embedded body. Names an author ONLY when the author is known.

    An unknown handle renders as ``[X] @: text`` — that is a fabricated
    attribution for a field that is simply absent, so the prefix is dropped.
    A retweet says who said it and who passed it on.
    """
    via = str(tweet.get("via_handle") or "").strip().lstrip("@")
    if tweet.get("is_retweet"):
        if via and handle:
            return f"[X] @{handle} (retweeted by @{via}): {text}"
        if handle:
            return f"[X] @{handle} (retweet): {text}"
        return f"[X] (retweet): {text}"
    return f"[X] @{handle}: {text}" if handle else f"[X] {text}"


def tweet_to_record(tweet: dict[str, Any]) -> dict[str, Any] | None:
    """Typed ingest record for one normalized tweet, routed to ``collection``.

    Returns None for dust (too-short text or missing URL) so the pipeline can
    filter with a single list comprehension.
    """
    text = str(tweet.get("text") or "").strip()
    handle = str(tweet.get("handle") or "").strip().lstrip("@")
    url = str(tweet.get("url") or "").strip()
    if len(text) < MIN_TWEET_CHARS or not url:
        return None
    return {
        "content": _content_line(tweet, text[:4000], handle),
        "collection": RAG_COLLECTION,
        "severity": "medium",
        "source": f"x.com/{handle}" if handle else "x.com",
        "metadata": {
            "kind": "x_retweet" if tweet.get("is_retweet") else "x_tweet",
            "handle": handle,
            "tweet_id": tweet.get("tweet_id"),
            "url": url,
            "ts": str(tweet.get("created_at") or ""),
            "like_count": tweet.get("like_count", 0),
            "retweet_count": tweet.get("retweet_count", 0),
            "lane": tweet.get("source", "getxapi"),
            "via_handle": str(tweet.get("via_handle") or ""),
            "conversation_id": str(tweet.get("conversation_id") or ""),
            "in_reply_to": str(tweet.get("in_reply_to") or ""),
            "in_reply_to_handle": str(tweet.get("in_reply_to_handle") or ""),
            "quoted_id": str(tweet.get("quoted_id") or ""),
            "quoted_handle": str(tweet.get("quoted_handle") or ""),
            "text_hash": text_fingerprint(text),
        },
    }


def tweets_to_docs(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map normalized tweets → ingest docs, dropping dust (fail-open)."""
    return [rec for t in tweets if (rec := tweet_to_record(t)) is not None]


__all__ = ["SEEN_TTL_S", "dedupe_seen", "text_fingerprint", "tweet_to_record", "tweets_to_docs"]

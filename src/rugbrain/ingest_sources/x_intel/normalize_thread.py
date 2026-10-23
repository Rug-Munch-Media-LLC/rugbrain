"""Status / thread / retweet normalization for the X ingest lane (pure, no I/O).

Split out of :mod:`normalize` so the parent module stays under the 200-line cap
and so the "who actually said this" rules live in one readable place.

The rules exist because the provider payload is not a post, it is a container:
a timeline row can be a retweet (the wrapper user is NOT the author), a reply
(one part of a thread), a quote (two authors in one row), or a long post whose
``text`` is a truncated preview of ``note_tweet.text``. Storing any of those
verbatim FABRICATES a post — the wrapper handle is published as the author and
the URL points at the wrapper's status while the words belong to someone else
(measured 2026-10-24: 1,821 x_social points, retweets attributed to the
retweeter, 44 of them duplicated with an empty handle).

Pure functions over plain dicts so the rules are testable without a transport.
"""

from __future__ import annotations

import re
from typing import Any

#: Providers truncate previews with these markers; a body ending in one is not
#: the post, it is a stub and must not be published as the full text.
TRUNCATION_MARKERS: tuple[str, ...] = ("…", "...", "https:…", "…https")

#: ``RT @handle: body`` — X's own compact retweet form.
_RT_RE = re.compile(r"^RT\s+@([A-Za-z0-9_]{1,15})\s*:\s*(.*)$", re.DOTALL)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return None


def full_text(tweet: dict[str, Any]) -> str:
    """The post's own text: ``note_tweet`` body, ``full_text``, then ``text``.

    ``text`` is often a preview (``…`` tail). Preferring the long-form body is
    what stops a stored post from being a truncated headline.
    """
    note = _first(tweet, "note_tweet", "noteTweet")
    candidates = [
        _first(_as_dict(note), "text"),
        _first(tweet, "full_text", "fullText"),
        _first(_as_dict(_first(tweet, "extended_tweet", "extendedTweet") or {}), "full_text"),
        _first(tweet, "text", "tweet_text", "content"),
    ]
    best = ""
    for candidate in candidates:
        text = str(candidate or "").strip()
        if len(text) > len(best):
            best = text
    return best


def retweet_origin(tweet: dict[str, Any]) -> dict[str, Any] | None:
    """The ORIGINAL post when *tweet* is a retweet, else None.

    Handles both shapes: a nested ``retweeted_status`` object (full origin, with
    its own author and metrics) and the compact ``RT @handle: body`` text form
    (origin author recoverable, id is NOT — never invent one).
    """
    nested = _first(tweet, "retweeted_status", "retweetedStatus")
    if isinstance(nested, dict):
        return nested
    match = _RT_RE.match(full_text(tweet))
    if match:
        return {"_rt_handle": match.group(1), "_rt_text": match.group(2).strip()}
    return None


def thread_fields(tweet: dict[str, Any]) -> dict[str, Any]:
    """Conversation / reply / quote linkage for one row.

    ``in_reply_to`` is the parent status id; ``conversation_id`` is the thread
    root. Both are carried so a thread can be reassembled (or at least ordered)
    instead of being stored as unrelated one-liners.
    """
    reply_to = _first(tweet, "in_reply_to_status_id", "in_reply_to_status_id_str", "inReplyToId")
    reply_user = _first(
        tweet, "in_reply_to_username", "in_reply_to_screen_name", "inReplyToUsername"
    )
    quoted = _as_dict(_first(tweet, "quoted", "quoted_tweet", "quoted_status") or {})
    quoted_author = _as_dict(_first(quoted, "author", "user") or {})
    return {
        "conversation_id": str(_first(tweet, "conversation_id", "conversationId") or ""),
        "in_reply_to": str(reply_to or ""),
        "in_reply_to_handle": str(reply_user or "").lstrip("@"),
        "is_reply": bool(reply_to),
        "quoted_id": str(_first(quoted, "id", "tweet_id", "id_str") or ""),
        "quoted_handle": str(
            _first(quoted_author, "username", "screen_name", "handle") or ""
        ).lstrip("@"),
    }


def normalize_status(raw: Any, *, source: str = "getxapi") -> dict[str, Any] | None:
    """Normalize a status/detail payload: the post plus its thread replies.

    Returns ``{tweet, replies, thread_id}`` where ``tweet`` is normalized by the
    caller's ``normalize_tweet`` (passed in through *normalize*) — this function
    only separates the containers so replies are never merged into the parent.
    """
    if not isinstance(raw, dict):
        return None
    body = _as_dict(_first(raw, "tweet", "status", "data") or raw)
    if not body:
        return None
    replies = _first(raw, "replies", "thread", "conversation")
    return {
        "tweet": body,
        "replies": [r for r in (replies or []) if isinstance(r, dict)],
        "thread_id": str(_first(body, "conversation_id", "conversationId", "id", "tweet_id") or ""),
        "source": source,
    }


def looks_truncated(text: str) -> bool:
    """True when *text* is a provider preview rather than a complete post."""
    body = (text or "").rstrip()
    return any(body.endswith(marker) for marker in TRUNCATION_MARKERS)


__all__ = [
    "TRUNCATION_MARKERS",
    "full_text",
    "looks_truncated",
    "normalize_status",
    "retweet_origin",
    "thread_fields",
]

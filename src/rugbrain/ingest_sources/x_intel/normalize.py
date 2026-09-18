"""Normalization — hostile provider payloads → one clean tweet/profile shape.

Ported from rugmunchbot ``getxapi.normalize_tweet``. Tolerates flat
``{id, text, ...}`` payloads and nested ``tweet``/``user`` objects; returns
None for rows without a tweet id + text.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tweet_url(handle: str, tweet_id: str) -> str:
    h = str(handle or "").strip().lstrip("@")
    if h and tweet_id:
        return f"https://x.com/{h}/status/{tweet_id}"
    return f"https://x.com/i/status/{tweet_id}" if tweet_id else ""


def normalize_tweet(raw: Any, *, source: str = "getxapi") -> dict[str, Any] | None:
    """Normalize one raw tweet payload into the shared tweet shape.

    Output: ``{tweet_id, text, handle, author_name, created_at,
    retweet_count, like_count, reply_count, quote_count, url, source}``.
    """
    if not isinstance(raw, dict):
        return None
    nested = raw.get("tweet")
    tweet: dict[str, Any] = nested if isinstance(nested, dict) else raw
    tweet_id = str(_first(tweet, "id", "tweet_id", "id_str", "rest_id", default="") or "").strip()
    text = str(
        _first(tweet, "text", "full_text", "tweet_text", "content", default="") or ""
    ).strip()
    if not text or not tweet_id:
        return None

    author = _as_dict(tweet.get("author"))
    user = _as_dict(tweet.get("user"))
    handle = (
        str(
            _first(author, "username", "screen_name", "handle", default="")
            or _first(user, "username", "screen_name", "handle", default="")
            or _first(tweet, "username", "screen_name", "handle", default="")
        )
        .strip()
        .lstrip("@")
    )
    author_name = (
        str(
            _first(author, "name", "display_name", "full_name", default="")
            or _first(user, "name", "display_name", default="")
            or _first(tweet, "name", "display_name", default="")
        ).strip()
        or handle
    )
    created = str(_first(tweet, "created_at", "createdAt", "timestamp", default="") or "").strip()

    metrics = tweet.get("engagement") if isinstance(tweet.get("engagement"), dict) else {}
    for key in ("public_metrics", "metrics"):
        if not metrics and isinstance(tweet.get(key), dict):
            metrics = tweet[key]
    if not metrics:
        metrics = {
            "retweet_count": _first(tweet, "retweet_count", "retweets", default=0),
            "like_count": _first(tweet, "like_count", "likes", "favorite_count", default=0),
            "reply_count": _first(tweet, "reply_count", "replies", default=0),
            "quote_count": _first(tweet, "quote_count", "quotes", default=0),
        }

    return {
        "tweet_id": tweet_id,
        "text": text[:500],
        "handle": handle,
        "author_name": author_name,
        "created_at": created,
        "retweet_count": _as_int(_first(metrics, "retweet_count", "retweets", "retweetCount")),
        "like_count": _as_int(
            _first(metrics, "like_count", "likes", "likeCount", "favorite_count")
        ),
        "reply_count": _as_int(_first(metrics, "reply_count", "replies", "replyCount")),
        "quote_count": _as_int(_first(metrics, "quote_count", "quotes")),
        "url": _tweet_url(handle, tweet_id),
        "source": source,
    }


def parse_tweets_payload(payload: Any) -> list[dict[str, Any]]:
    """Pull every tweet-shaped dict out of a GetXAPI payload (tolerant walk).

    Walks common container keys and list values; dedupes by tweet id.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            norm = normalize_tweet(value)
            if norm is not None:
                if norm["tweet_id"] not in seen:
                    seen.add(norm["tweet_id"])
                    out.append(norm)
                return
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)

    _walk(payload)
    return out


def normalize_profile(raw: Any, *, source: str = "getxapi") -> dict[str, Any] | None:
    """Normalize a user-info payload: ``{username, name, bio, followers_count,
    following_count, tweets_count, source}``."""
    if not isinstance(raw, dict):
        return None
    user = _as_dict(raw.get("user")) or raw
    username = (
        str(_first(user, "username", "screen_name", "handle", default="") or "").strip().lstrip("@")
    )
    if not username:
        return None
    return {
        "username": username,
        "name": str(_first(user, "name", "display_name", "full_name", default="") or "").strip()
        or username,
        "bio": str(_first(user, "bio", "description", default="") or "").strip()[:300],
        "followers_count": _as_int(_first(user, "followers_count", "followers", "follower_count")),
        "following_count": _as_int(_first(user, "following_count", "following")),
        "tweets_count": _as_int(_first(user, "tweets_count", "statuses_count", "media_count")),
        "source": source,
    }


__all__ = ["normalize_profile", "normalize_tweet", "parse_tweets_payload"]

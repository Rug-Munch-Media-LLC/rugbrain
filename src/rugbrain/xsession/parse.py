"""Response validation + parsing for the X GraphQL lane.

Every payload is validated before anything downstream sees it: a missing
``data.user`` or an ``errors`` array raises ``XSchemaError`` carrying X's own
error codes, so a schema flip surfaces as a hard failure instead of an empty
feed that looks like "no news today".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

TIME_FORMAT = "%a %b %d %H:%M:%S %z %Y"


class XSchemaError(RuntimeError):
    """The response did not match the expected GraphQL shape."""


def _unpack(payload: dict[str, Any]) -> dict[str, Any]:
    errors = payload.get("errors")
    if errors:
        codes = [e.get("code") for e in errors if isinstance(e, dict)]
        messages = [str(e.get("message"))[:120] for e in errors if isinstance(e, dict)]
        raise XSchemaError(f"api errors {codes}: {messages}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise XSchemaError("payload has no 'data' object")
    return data


def parse_user(payload: dict[str, Any]) -> dict[str, Any]:
    """Primary parser: ``UserByScreenName`` → flat user record."""
    data = _unpack(payload)
    user = (data.get("user") or {}).get("result")
    if not isinstance(user, dict) or not user.get("rest_id"):
        reason = (data.get("user") or {}).get("reason") or "unavailable"
        raise XSchemaError(f"user unavailable: {reason}")
    core = user.get("core") or {}
    legacy = user.get("legacy") or {}
    return {
        "user_id": str(user["rest_id"]),
        "screen_name": core.get("screen_name") or legacy.get("screen_name"),
        "name": core.get("name") or legacy.get("name"),
        "followers": int(legacy.get("followers_count") or 0),
        "following": int(legacy.get("friends_count") or 0),
        "tweets": int(legacy.get("statuses_count") or 0),
        "created_at": legacy.get("created_at"),
        "verified": bool(user.get("is_blue_verified") or legacy.get("verified")),
        "protected": bool(legacy.get("protected")),
    }


def parse_created_at(value: Any) -> datetime | None:
    """X timestamps are 'Thu Oct 15 17:09:33 +0000 2026'."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        return None


def _tweet_from_results(results: Any) -> dict[str, Any] | None:
    if not isinstance(results, dict):
        return None
    node = results.get("result") or {}
    if node.get("__typename") == "TweetWithVisibilityResults":
        node = node.get("tweet") or {}
    legacy = node.get("legacy") or {}
    tweet_id = node.get("rest_id") or legacy.get("id_str")
    text = legacy.get("full_text") or ""
    note = (((node.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {})
    if note.get("text"):
        text = note["text"]
    if not tweet_id or not text:
        return None
    return {
        "tweet_id": str(tweet_id),
        "text": text.strip(),
        "created_at": parse_created_at(legacy.get("created_at")),
        "likes": int(legacy.get("favorite_count") or 0),
        "retweets": int(legacy.get("retweet_count") or 0),
        "replies": int(legacy.get("reply_count") or 0),
        "is_retweet": bool(legacy.get("retweeted_status_result") or text.startswith("RT @")),
        "lang": legacy.get("lang"),
    }


def parse_tweets(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Primary parser: ``UserTweets`` → (tweets, next cursor).

    Handles every entry shape the timeline mixes in: bare tweets,
    ``profile-conversation`` thread modules, and the bottom cursor.
    """
    data = _unpack(payload)
    result = ((data.get("user") or {}).get("result") or {})
    timeline = result.get("timeline_v2") or result.get("timeline") or {}
    instructions = (timeline.get("timeline") or {}).get("instructions") or []
    entries: list[dict[str, Any]] = []
    for instruction in instructions:
        entries.extend(instruction.get("entries") or [])

    tweets: list[dict[str, Any]] = []
    cursor: str | None = None
    for entry in entries:
        entry_id = str(entry.get("entryId") or "")
        content = entry.get("content") or {}
        if entry_id.startswith("cursor-bottom"):
            cursor = content.get("value") or cursor
            continue
        if entry_id.startswith("tweet-"):
            tweet = _tweet_from_results(content.get("itemContent", {}).get("tweet_results"))
            if tweet:
                tweets.append(tweet)
            continue
        if entry_id.startswith("profile-conversation"):
            for item in content.get("items") or []:
                tweet = _tweet_from_results(
                    (((item.get("item") or {}).get("itemContent")) or {}).get("tweet_results")
                )
                if tweet:
                    tweets.append(tweet)
    return tweets, cursor

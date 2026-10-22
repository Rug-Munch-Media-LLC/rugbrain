"""GraphQL endpoint wrappers — parameters in, validated records out."""

from __future__ import annotations

import json
from typing import Any

from rugbrain.xsession import config
from rugbrain.xsession.client import XSessionClient
from rugbrain.xsession.parse import parse_tweets, parse_user


def _q(**kwargs: Any) -> str:
    return json.dumps(kwargs, separators=(",", ":"))


async def resolve_user(client: XSessionClient, handle: str) -> dict[str, Any]:
    """Primary entry point: handle → validated user record."""
    clean = handle.strip().lstrip("@")
    payload = await client.graphql(
        "UserByScreenName",
        config.QUERY_USER_BY_SCREEN_NAME,
        {
            "variables": _q(screen_name=clean, withSafetyModeUserFields=True),
            "features": _q(**config.USER_FEATURES),
        },
    )
    return parse_user(payload)


async def user_tweets(
    client: XSessionClient,
    user_id: str,
    cursor: str | None = None,
    count: int = 40,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of a user's timeline plus the next cursor."""
    variables: dict[str, Any] = {
        "userId": str(user_id),
        "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    payload = await client.graphql(
        "UserTweets",
        config.QUERY_USER_TWEETS,
        {
            "variables": _q(**variables),
            "features": _q(**config.TWEET_FEATURES),
            "fieldToggles": _q(withArticlePlainText=False),
        },
    )
    return parse_tweets(payload)


async def timeline(
    client: XSessionClient,
    handle: str,
    max_pages: int | None = None,
    count: int = 40,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Primary ingest unit: user record + up to ``max_pages`` of their tweets."""
    user = await resolve_user(client, handle)
    pages = config.MAX_TIMELINE_PAGES if max_pages is None else max(1, max_pages)
    tweets: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(pages):
        batch, cursor = await user_tweets(client, user["user_id"], cursor, count)
        tweets.extend(batch)
        if not cursor or not batch:
            break
    seen: set[str] = set()
    unique = [t for t in tweets if not (t["tweet_id"] in seen or seen.add(t["tweet_id"]))]
    return user, unique

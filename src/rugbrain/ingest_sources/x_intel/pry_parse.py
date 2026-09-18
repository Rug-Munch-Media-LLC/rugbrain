"""Markdown parsing helpers for the Pry scrape lane (pure, no I/O).

Pry returns rendered X pages as markdown; these helpers pull the tweet body,
engagement counts, timestamp, and URL identifiers out of that markdown.
Kept separate from ``pry_lane`` so each module stays small and testable.
"""

from __future__ import annotations

import re
from typing import Any


def parse_int(value: Any) -> int:
    """Best-effort int parse (strips commas / K / M suffixes). Fail-open 0."""
    if value is None or isinstance(value, bool):
        return int(value or 0) if isinstance(value, bool) else 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    mult = 1
    if text[-1:].lower() == "k":
        mult, text = 1000, text[:-1]
    elif text[-1:].lower() == "m":
        mult, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except (TypeError, ValueError):
        return 0


def extract_engagement(markdown: str) -> dict[str, int]:
    """Pull like / retweet / reply counts out of a rendered X page."""
    text = markdown or ""

    def _find(labels: tuple[str, ...]) -> int:
        for label in labels:
            m = re.search(r"([\d.,]+[kKmM]?)\s*" + re.escape(label), text, re.IGNORECASE)
            if m:
                return parse_int(m.group(1))
        return 0

    return {
        "like_count": _find(("likes", "like", "hearts", "favorites")),
        "retweet_count": _find(("reposts", "retweets", "retweet")),
        "reply_count": _find(("replies", "reply", "comments")),
    }


def extract_tweet_text(markdown: str) -> str:
    """Best-effort tweet body extraction from a rendered X page."""
    text = (markdown or "").strip()
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and re.fullmatch(r"@?[\w.]+", lines[0]):
        lines = lines[1:]
    body = " ".join(lines)
    for marker in ("Show more", "·", "Replying to", "Retweeted"):
        idx = body.find(marker)
        if idx > 0:
            body = body[:idx]
    m = re.search(
        r"\s[\d.,]+[kKmM]?\s*(?:Likes?|Reposts?|Retweets?|Replies?|Comments?)\s*$",
        body,
        re.IGNORECASE,
    )
    if m:
        body = body[: m.start()]
    return body.strip()[:500]


def extract_created_at(markdown: str) -> str:
    """Best-effort tweet timestamp extraction (ISO or X native)."""
    text = markdown or ""
    m = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?", text)
    if m:
        return m.group(0)
    m = re.search(r"\d{1,2}:\d{2}\s*[APap][Mm]\s*[·•]\s*[A-Za-z]{3}\s+\d{1,2},\s+\d{4}", text)
    return m.group(0) if m else ""


def tweet_id_from_url(tweet_url: str) -> str:
    """Extract the numeric status id from an X tweet URL ("" when absent)."""
    m = re.search(r"/status/(\d+)", tweet_url or "")
    return m.group(1) if m else ""


def handle_from_url(tweet_url: str) -> str:
    """Extract the author handle from an X tweet URL ("" when absent)."""
    m = re.search(r"x\.com/([^/]+)/status/", tweet_url or "")
    return m.group(1).strip().lstrip("@") if m else ""


__all__ = [
    "extract_created_at",
    "extract_engagement",
    "extract_tweet_text",
    "handle_from_url",
    "parse_int",
    "tweet_id_from_url",
]

"""YouTube channel Atom feed → video entries.

One unauthenticated GET per channel, no API key, no JavaScript: the channel feed
already carries the video id, title, channel name and published_at, which is all
the pipeline needs before a transcript is attempted.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from rugbrain.ingest_sources.content_sources.spec import SourceSpec

__all__ = ["parse_channel_feed"]

_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
_SUMMARY_CAP = 2000


def _tag(block: str, tag: str) -> str:
    """First ``<tag>`` text in ``block``, unescaped and stripped."""
    match = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", block, re.DOTALL)
    return unescape(match.group(1)).strip() if match else ""


def parse_channel_feed(xml: str, spec: SourceSpec) -> list[dict[str, Any]]:
    """Parse a channel's Atom feed into flat entries.

    A malformed or empty feed is an empty list, never an exception: one dead
    channel must not stop the batch.
    """
    entries: list[dict[str, Any]] = []
    for block in _ENTRY_RE.findall(xml or ""):
        video_id = _tag(block, "yt:videoId")
        title = _tag(block, "title")
        if not video_id or not title:
            continue
        entries.append(
            {
                "title": title,
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "channel": _tag(block, "name") or spec.publisher,
                "published_at": _tag(block, "published"),
                "summary": _tag(block, "media:description")[:_SUMMARY_CAP],
            }
        )
    return entries

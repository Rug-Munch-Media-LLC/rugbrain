"""Story identity for news ingest — the transport is not the story.

``rss_store.content_hash`` mixes ``guid or link`` into the digest, so the same
wire story republished by two outlets gets two identities and is stored twice
(measured: 4,870 such rows in news_v2_8b). This key hashes what makes the story
the story: the normalised title, plus a normalised body prefix when one exists.
The link is only a tiebreaker for untitled items, where there is nothing else
to identify the row by.
"""

from __future__ import annotations

import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_URL = re.compile(r"https?://\S+")
_WS = re.compile(r"\s+")

#: How much of the body contributes to identity. Enough to separate two stories
#: that share a headline, short enough that a trailing correction does not.
BODY_CHARS = 280


def _normalise(text: str) -> str:
    text = _URL.sub(" ", (text or "").lower())
    return _WS.sub(" ", _NON_ALNUM.sub("", text)).strip()


def story_key(title: str, body: str, link: str) -> str:
    """Stable identity for one story, independent of where it was published."""
    title_norm = _normalise(title)
    if title_norm:
        material = f"{title_norm}|{_normalise(body)[:BODY_CHARS]}"
    else:
        material = _normalise(body)[:BODY_CHARS] or _normalise(link)
    return hashlib.sha256(material.encode()).hexdigest()[:32]

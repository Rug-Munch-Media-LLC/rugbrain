"""Refusal gates for URL ingestion: size, type, robots, paywall.

Pure and synchronous on purpose — the transport fetches bytes, robots.txt and
content type; ``check_url_allowed`` only decides. A refusal is a value, never an
exception, so a caller can record why a page never entered the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

MAX_BYTES = 2 * 1024 * 1024
UA = "RugBrainIngest/1.0 (+https://rugmunch.io/bot)"
PAYWALL_MARKERS = (
    "subscribe to continue",
    "this article is for subscribers",
    "sign in to continue reading",
    "subscriber-only",
    "create a free account to continue",
    "reached your limit of free articles",
)
_TEXTUAL = ("text/", "application/xhtml", "application/pdf")


@dataclass(frozen=True)
class UrlVerdict:
    allowed: bool
    reason: str = ""
    size_bytes: int = 0


def check_url_allowed(
    url: str,
    payload: bytes,
    *,
    robots_text: str | None = None,
    content_type: str = "",
    max_bytes: int = MAX_BYTES,
) -> UrlVerdict:
    """Return the first refusal that applies, or ``allowed=True``."""
    size = len(payload or b"")
    if size > max_bytes:
        return UrlVerdict(False, "too_large", size)
    ctype = (content_type or "").lower()
    if ctype and not any(ctype.startswith(t) for t in _TEXTUAL):
        return UrlVerdict(False, "unsupported_type", size)
    if robots_text is not None:
        parser = RobotFileParser()
        parser.parse(robots_text.splitlines())
        if not parser.can_fetch(UA, url):
            return UrlVerdict(False, "robots_disallowed", size)
    head = (payload or b"")[:200_000].decode("utf-8", "ignore").lower()
    if any(marker in head for marker in PAYWALL_MARKERS):
        return UrlVerdict(False, "paywall", size)
    return UrlVerdict(True, "", size)

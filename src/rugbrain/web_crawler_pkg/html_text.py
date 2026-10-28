"""Lightweight HTML → text fallback used when crawl4ai is unavailable.

Moved verbatim from ``web_crawler.py``.
"""

from __future__ import annotations

import html
import re

# ── HTML → text fallback extraction ──────────────────────────────────────

_SKIP_TAGS_RE = re.compile(
    r"<(script|style|nav|footer|header|aside|form|iframe|svg)[^>]*>.*?</\1>", re.S | re.I
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw: str, max_chars: int = 20000) -> str:
    """Lightweight HTML-to-text used when crawl4ai is unavailable."""
    text = _SKIP_TAGS_RE.sub(" ", raw or "")
    text = _ANY_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()[:max_chars]

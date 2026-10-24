"""Story timestamp normalisation — one parser for every source's date dialect.

Three writers, three dialects. Telegram ingest stamps ISO-8601; the X lane
keeps the raw tweet format (``Wed Sep 16 18:18:29 +0000 2026``); RSS feeds
publish ISO from Atom and RFC 2822 from RSS 2.0. The bot's recency gate parses
``ts`` with :func:`datetime.fromisoformat` and fails CLOSED, so an unparsed
dialect is a story that can never post — a silent, total loss for that source.

Measured 2026-10-24: ``news_v2_8b`` holds 1,821 ``x_social`` points, every one
stamped in the tweet dialect, so the X lane reached the candidate pool and was
dropped by the gate every single cycle. ``research_v2_8b`` carries
``published`` and no ``ts`` at all, so RSS research never had a timestamp to
gate on.

One dialect table, one function: a new source adds a format HERE, not a branch
at every call site.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

#: Non-ISO dialects found in live payloads, cheapest first.
_DIALECTS: tuple[str, ...] = (
    "%a %b %d %H:%M:%S %z %Y",  # X / tweet format
    "%a, %d %b %Y %H:%M:%S %z",  # RFC 2822 with weekday
    "%Y-%m-%d %H:%M:%S",  # naive SQL timestamp
    "%Y-%m-%d",
)


def _parse(raw: str) -> datetime | None:
    """Try ISO first (the common case), then the declared dialects."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _DIALECTS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def to_iso8601(value: object) -> str:
    """Normalise any known story timestamp to ISO-8601, or "" when unparseable.

    "" is the contract for "no recency signal" — the caller must fail closed,
    never invent a timestamp. Naive values are stamped UTC because every writer
    in this pipeline stores UTC.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = _parse(raw)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


__all__ = ["to_iso8601"]

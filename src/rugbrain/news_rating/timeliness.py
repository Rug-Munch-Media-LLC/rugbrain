"""Timeliness: datetime parsing and freshness/decay scoring."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from rugbrain.news_rating.axes import _clamp

logger = logging.getLogger(__name__)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse datetime/date/ISO-string/unix-timestamp inputs, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            logger.debug("timeliness: bad timestamp value %r", value)
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            logger.debug("timeliness: unparseable ISO datetime %r", text)
            return None
    return None


def _age_hours(parsed: datetime, now: datetime) -> float:
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def timeliness_score(
    published_at: Any,
    event_at: Any = None,
    now: datetime | None = None,
) -> int:
    """0-100: fresh reporting scores high, stale re-reporting scores low.

    Piecewise decay on article age. When ``event_at`` is supplied and the
    article reports an event older than 72h, a re-report penalty applies.
    Missing ``published_at`` returns a neutral 50.
    """
    parsed = _parse_datetime(published_at)
    if parsed is None:
        return 50
    now_dt = now or datetime.now(UTC)
    age = _age_hours(parsed, now_dt)

    if age < 1:
        score = 100
    elif age < 6:
        score = 95
    elif age < 24:
        score = 88
    elif age < 72:
        score = 72
    elif age < 168:
        score = 55
    elif age < 720:
        score = 35
    else:
        score = 15

    event = _parse_datetime(event_at)
    if event is not None and event <= parsed:
        lag_hours = (parsed - event).total_seconds() / 3600.0
        if lag_hours > 72:
            score -= 15
    return _clamp(score)

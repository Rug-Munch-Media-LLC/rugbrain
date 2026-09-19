"""Small numeric/text normalization primitives shared by fear-index modules."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def _parse_dt(value: Any) -> datetime | None:
    """Parse datetime/ISO string values, else None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _as_int_or_none(value: Any) -> int | None:
    """Coerce a component value to 0-100 int; None for missing/errored."""
    if value is None or isinstance(value, (Exception, bool)):
        return None
    if isinstance(value, (int, float)):
        return _clamp(float(value))
    return None


def _term_hit(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms)

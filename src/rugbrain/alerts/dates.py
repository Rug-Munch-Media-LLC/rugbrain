"""Date helpers shared by the alert pollers."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()


def _within_window(date_iso: str, now: float, days: float) -> bool:
    if not date_iso:
        return False
    try:
        d = datetime.fromisoformat(date_iso).date()
        age_days = (datetime.fromtimestamp(now, tz=UTC).date() - d).days
        return 0 <= age_days <= days
    except ValueError:
        return False


def _parse_date_iso(raw: str) -> str:
    """Parse pubDate (RFC-822) or ISO dates into YYYY-MM-DD (best effort)."""
    s = (raw or "").strip()
    if not s:
        return ""
    # RFC-822: "Tue, 04 Aug 2026 12:00:00 +0000"
    m = re.search(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", s, re.I
    )
    if m:
        try:
            day = int(m.group(1))
            month = _MONTHS[m.group(2).lower()]
            year = int(m.group(3))
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""

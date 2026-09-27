"""Date/chain normalisation helpers for the crypto payload backfill.

READ-ONLY design: nothing in this module opens a write connection. It is the
pure logic half of ``backfill_payload_date.py`` and is imported by its tests.

Normalisation contract
----------------------
``canonical_date`` emits **RFC 3339 UTC** — ``2024-04-15T00:00:00Z``. Qdrant's
datetime range filter parses RFC 3339 and compares as a real date, not a string.
The existing corpus mostly stores either ``YYYY-MM-DD`` (lexicographically safe)
or ``2021-6-20`` (**not** safe: ``6`` > ``0`` so June sorts after September), so
every value is re-rendered rather than copied.

``canonical_chain`` lowercases and trims. Empty, ``unknown`` and junk are
normalised to ``""`` so "chain == base" cannot be satisfied by ``'Base'`` or
``'ticker'`` while a real value is present elsewhere in the same corpus.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Padded and unpadded ISO dates. Qdrant rejects unpadded; padding is the fix.
# The time group is separate and non-greedy-safe: folding it into the day group
# makes ``2023-08-13T00:00:00Z`` parse as day=13 then a ``T00`` UTC offset, which
# silently shifts the timestamp by hours.
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_ISO_TIME = re.compile(r"[T ](\d{1,2}):(\d{2})(?::(\d{2}))?")
_SLASH_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_NUMERIC_TS = re.compile(r"^\d{9,11}(?:\.\d+)?$")

_UNSET_CHAINS = frozenset({"", "unknown", "n/a", "na", "none", "null", "multi", "multichain"})


def _from_epoch(value: float) -> str:
    return datetime.fromtimestamp(float(value), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_parts(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> str:
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return ""
    return _render(moment)


def _render(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_parts(text: str) -> tuple[int, int, int, int, int, int]:
    """Extract Y/M/D H:M:S from an ISO-ish string, defaulting the time to 00:00.

    Naive timestamps are treated as UTC — the whole corpus writes ``+00:00`` or
    ``Z``, and inventing a local offset here would move real event times.
    """
    match = _ISO_DATE.search(text)
    if not match:
        raise ValueError(f"no iso date in {text[:32]!r}")
    stamp = _ISO_TIME.search(text)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(stamp.group(1)) if stamp else 0,
        int(stamp.group(2)) if stamp else 0,
        int(stamp.group(3)) if stamp and stamp.group(3) else 0,
    )


def canonical_date(*candidates: Any) -> str:
    """Return the first candidate that normalises to an RFC 3339 UTC date, else "".

    Accepts datetimes, epoch seconds, ``YYYY-M-D`` (padded or not), ``M/D/YYYY``
    and full ISO timestamps. Candidates are tried in order, so pass the most
    trustworthy source first.
    """
    for raw in candidates:
        if raw is None:
            continue
        if isinstance(raw, datetime):
            moment = raw if raw.tzinfo else raw.replace(tzinfo=UTC)
            return _render(moment)
        text = str(raw).strip()
        if not text:
            continue
        if _NUMERIC_TS.match(text):
            try:
                return _from_epoch(float(text))
            except (ValueError, OverflowError, OSError):
                continue
        match = _ISO_DATE.search(text)
        if match:
            try:
                # Explicit calendar via regex, not fromisoformat: the latter
                # rejects the unpadded and M/D/YYYY shapes the corpus contains.
                return _from_parts(*_iso_parts(text))
            except ValueError:
                continue
        match = _SLASH_DATE.search(text)
        if match:
            stamped = _from_parts(int(match.group(3)), int(match.group(1)), int(match.group(2)))
            if stamped:
                return stamped
    return ""


def canonical_chain(value: Any) -> str:
    """Lowercase a chain token; return "" for absent, unknown and non-chains."""
    text = str(value or "").strip().lower()
    return "" if text in _UNSET_CHAINS else text


def dig(payload: dict[str, Any], path: str) -> Any:
    """Fetch a dotted path from a payload without raising on a missing hop."""
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current

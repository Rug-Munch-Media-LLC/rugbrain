"""Freshness contract for prediction-market odds.

A market's odds are a snapshot, never a live fact. Anything rendered from the
corpus must say *how old* the snapshot is and must call it ``stale`` once it
falls outside ``STALE_AFTER_HOURS``. 6h is the default because Polymarket
prices move intraday and a corpus slice is refreshed on a timer, not on read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

STALE_AFTER_HOURS: float = 6.0
LABEL_CURRENT = "current"
LABEL_STALE = "stale"


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def freshness(
    fetched_at: datetime | str,
    now: datetime | None = None,
    stale_after_hours: float = STALE_AFTER_HOURS,
) -> dict[str, object]:
    """Primary entry point: is this snapshot current or stale, and by how much."""
    fetched = _as_utc(fetched_at)
    reference = _as_utc(now) if now is not None else datetime.now(UTC)
    age = (reference - fetched).total_seconds() / 3600.0
    stale = age > stale_after_hours
    return {
        "label": LABEL_STALE if stale else LABEL_CURRENT,
        "age_hours": round(age, 2),
        "stale_after_hours": stale_after_hours,
        "fetched_at": fetched.isoformat().replace("+00:00", "Z"),
        "expires_at": (fetched + timedelta(hours=stale_after_hours)).isoformat().replace("+00:00", "Z"),
        "stale": stale,
    }


def annotate(
    text: str,
    fetched_at: datetime | str,
    now: datetime | None = None,
    stale_after_hours: float = STALE_AFTER_HOURS,
) -> str:
    """Answer-time labelling: append an explicit provenance/freshness footer."""
    state = freshness(fetched_at, now=now, stale_after_hours=stale_after_hours)
    if state["stale"]:
        footer = (
            f"[STALE] odds are {state['age_hours']}h old (fetched {state['fetched_at']}, "
            f"stale after {state['stale_after_hours']}h) — not current"
        )
    else:
        footer = f"[CURRENT] odds as of {state['fetched_at']} ({state['age_hours']}h old)"
    return f"{text.rstrip()}\n{footer}"


__all__ = ["LABEL_CURRENT", "LABEL_STALE", "STALE_AFTER_HOURS", "annotate", "freshness"]

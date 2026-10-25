"""Typed live envelope — every live call returns this, never a bare list or None."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

PRICE_STALE_AFTER_S = 120.0
NEWS_STALE_AFTER_S = 86400.0


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def coerce_ts(value: Any) -> datetime | None:
    """Accept epoch seconds/millis, ISO-8601, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def age_seconds(fetched_at: str | None) -> float | None:
    parsed = coerce_ts(fetched_at)
    return None if parsed is None else (utcnow() - parsed).total_seconds()


@dataclass
class Envelope:
    ok: bool
    data: Any = None
    source: str = ""
    fetched_at: str = field(default_factory=lambda: iso(utcnow()))
    stale: bool = False
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ok_envelope(source: str, data: Any, fetched_at: str | None = None, stale: bool = False) -> Envelope:
    return Envelope(
        ok=True,
        data=data,
        source=source,
        fetched_at=fetched_at or iso(utcnow()),
        stale=stale,
    )


def refuse(code: str, reason: str, source: str, fetched_at: str | None = None) -> Envelope:
    return Envelope(
        ok=False,
        data=None,
        source=source,
        fetched_at=fetched_at or iso(utcnow()),
        stale=code == "stale",
        error={"code": code, "reason": reason},
    )

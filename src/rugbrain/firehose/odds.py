"""Prediction-market odds → a trader-readable record, with an explicit staleness label.

Polymarket's gamma payload is stringly typed (``outcomePrices`` and ``outcomes``
are JSON *strings*, ``volume`` is a string). Parsing it inline in a route is how
silent ``None`` probabilities leak into an answer, so it lives here: one pure
function, no I/O, and every unparseable field surfaces as ``None`` rather than a
guess. Anything older than :data:`STALE_AFTER_HOURS` is labelled ``stale`` — an
odds answer without a freshness label is a lie by omission.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

#: The fleet's freshness bar: older than six hours is stale, no exceptions.
STALE_AFTER_HOURS = 6.0


def _json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _age_hours(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() / 3600.0


def parse_market(market: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """One gamma market → ``{question, outcomes, implied_prob, volume_usd, end_date, updated, age_h, freshness}``."""
    now = now or datetime.now(UTC)
    names = [str(o) for o in _json_list(market.get("outcomes"))]
    prices = [float(p) for p in _json_list(market.get("outcomePrices")) if str(p) not in ("", "None")]
    prob = dict(zip(names, prices)) if len(names) == len(prices) else {}
    volume = market.get("volumeNum", market.get("volume"))
    try:
        volume_usd = float(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume_usd = None
    updated = market.get("updatedAt") or market.get("startDate")
    age = _age_hours(updated, now)
    return {
        "question": market.get("question"),
        "outcomes": names,
        "implied_prob": prob,
        "volume_usd": volume_usd,
        "end_date": market.get("endDate") or market.get("endDateIso"),
        "updated": updated,
        "age_h": None if age is None else round(age, 3),
        "freshness": "stale" if age is None or age > STALE_AFTER_HOURS else "fresh",
        "closed": bool(market.get("closed")),
    }


__all__ = ["STALE_AFTER_HOURS", "parse_market"]

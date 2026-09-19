"""In-memory history ring, windowed samples and history queries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("rugbrain.fear_index")


def _record(snapshot: dict[str, Any]) -> None:
    """Append a snapshot to the in-process history ring."""
    from rugbrain import fear_index as _pkg

    _pkg._history.append(
        {
            "ts": snapshot.get("timestamp"),
            "value": snapshot.get("value"),
            "classification": snapshot.get("classification"),
        }
    )
    _pkg._last_snapshot = snapshot


def _window_seconds(window: str) -> int:
    from rugbrain import fear_index as _pkg

    return _pkg._WINDOW_SECONDS.get(str(window).lower(), 86400)


async def _samples_for_window(window: str) -> list[dict[str, Any]]:
    """Window-filtered samples from memory, merged with ClickHouse rows."""
    from rugbrain import fear_index as _pkg

    seconds = _window_seconds(window)
    cutoff = datetime.now(UTC) - timedelta(seconds=seconds)
    seen: dict[str, dict[str, Any]] = {}
    for entry in _pkg._history:
        ts = _pkg._parse_dt(entry.get("ts"))
        if ts is None or ts < cutoff:
            continue
        seen[ts.isoformat()] = entry
    try:
        rows = _pkg._storage.query_history(max(1, round(seconds / 86400)))
        for row in rows:
            ts = _pkg._parse_dt(row.get("ts"))
            if ts is None or ts < cutoff:
                continue
            seen[ts.isoformat()] = row
    except Exception as exc:
        log.debug("fear_index_window_clickhouse_merge_failed: %s", exc)
    return [seen[key] for key in sorted(seen)]


async def fear_index_history(days: int = 30) -> dict[str, Any]:
    """Historical chart points, ClickHouse-first with memory fallback."""
    from rugbrain import fear_index as _pkg

    try:
        rows = _pkg._storage.query_history(days)
        if rows:
            return {"days": days, "points": rows, "source": "clickhouse"}
    except Exception as exc:
        log.debug("fear_index_history_clickhouse_failed: %s", exc)
    if _pkg._history:
        points = [
            {"ts": entry["ts"], "value": entry["value"], "classification": entry["classification"]}
            for entry in _pkg._history
        ]
        return {"days": days, "points": points, "source": "memory"}
    return {"days": days, "points": [], "source": "empty"}

"""0-day / exploit / bounty read surface for the rugbrain-mcp facade.

Reads the same store the CLI writes (``ZERODAY_STORE`` or ``data/zeroday``),
so an agent and an operator never see different numbers.
"""

from __future__ import annotations

from typing import Any

from rugbrain.rugbrain_mcp.app import mcp
from rugbrain.zeroday.schema import SEVERITY_LEVEL
from rugbrain.zeroday.store import Store


def _rows(
    kind: str | None = None,
    min_severity: str | None = None,
    limit: int = 20,
    min_reward: float = 0.0,
) -> list[dict[str, Any]]:
    floor = SEVERITY_LEVEL.get(min_severity or "unknown", -1)
    kept = [
        row
        for row in Store().read_records()
        if (kind is None or row.get("kind") == kind)
        and SEVERITY_LEVEL.get(str(row.get("severity")), -1) >= floor
        and float(row.get("reward_usd") or 0.0) >= min_reward
    ]
    kept.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
    return kept[: max(1, min(limit, 100))]


@mcp.tool(name="rugbrain_zeroday")
def rugbrain_zeroday(
    kind: str | None = None, min_severity: str | None = "high", limit: int = 20
) -> dict[str, Any]:
    """Recent 0-day / exploit / bounty records from the zeroday pipeline.

    Args:
        kind: Filter by kind (0day, exploit, bounty, contest).
        min_severity: Minimum severity (critical, high, medium, low, info).
        limit: Max records (1-100, default 20).
    """
    rows = _rows(kind=kind, min_severity=min_severity, limit=limit)
    return {
        "count": len(rows),
        "items": rows,
        "store": Store().summary(),
        "note": "Sorted by published_at; empty means no sweep has run yet.",
    }


@mcp.tool(name="rugbrain_bounty_scope")
def rugbrain_bounty_scope(
    protocol: str | None = None, min_reward: float = 0.0, limit: int = 20
) -> dict[str, Any]:
    """Open bounty/contest programs with known scope and reward.

    Args:
        protocol: Case-insensitive substring match on title or protocol.
        min_reward: Minimum stated reward in USD (0 = no floor).
        limit: Max programs (1-100, default 20).
    """
    rows = [
        row
        for row in _rows(limit=100, min_reward=min_reward)
        if row.get("kind") in ("bounty", "contest")
    ]
    if protocol:
        needle = protocol.strip().lower()
        rows = [
            row
            for row in rows
            if needle in str(row.get("title", "")).lower()
            or needle in str(row.get("protocol") or "").lower()
            or needle in str(row.get("url", "")).lower()
        ]
    rows = rows[: max(1, min(limit, 100))]
    return {
        "count": len(rows),
        "programs": [
            {
                "program": row.get("title"),
                "source": row.get("source"),
                "url": row.get("url"),
                "reward_usd": row.get("reward_usd"),
                "scope": row.get("scope"),
                "confidence": row.get("confidence"),
            }
            for row in rows
        ],
        "note": "Reward/scope are null when the public listing does not state them.",
    }


__all__ = ["rugbrain_bounty_scope", "rugbrain_zeroday"]

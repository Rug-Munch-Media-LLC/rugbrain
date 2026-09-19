"""Composite fear-index computation and snapshot assembly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def compute_fear_index(components: dict[str, int | None]) -> int:
    """Weighted 0-100 composite; renormalizes over available components.

    Returns neutral 50 when no component is available.
    """
    from rugbrain import fear_index as _pkg

    available = {
        key: value
        for key, value in components.items()
        if value is not None and not isinstance(value, bool)
    }
    if not available:
        return 50
    total_weight = sum(_pkg.COMPONENT_WEIGHTS.get(key, 0.0) for key in available)
    if total_weight <= 0:
        return 50
    weighted = sum(
        _pkg.COMPONENT_WEIGHTS.get(key, 0.0) * float(available[key]) for key in available
    )
    return _pkg._clamp(weighted / total_weight)


def fear_snapshot(components: dict[str, int | None], now: datetime | None = None) -> dict[str, Any]:
    """Build a full snapshot dict from raw components."""
    from rugbrain import fear_index as _pkg

    value = compute_fear_index(components)
    return {
        "value": value,
        "classification": _pkg.classify_fear(value),
        "components": {key: (int(v) if v is not None else None) for key, v in components.items()},
        "timestamp": (now or datetime.now(UTC)).isoformat(),
    }

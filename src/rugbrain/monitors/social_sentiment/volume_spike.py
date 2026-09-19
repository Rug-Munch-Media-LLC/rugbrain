"""Social volume spike detection against a rolling average."""

from __future__ import annotations

from typing import Any


def detect_social_volume_spike(
    symbol: str,
    current_volume: int,
) -> dict[str, Any] | None:
    """Detect if current social volume is a spike relative to rolling average.

    Returns spike info dict if volume > VOLUME_SPIKE_MILD_THRESHOLD * rolling avg,
    or None if no spike.
    """
    from rugbrain.monitors import social_sentiment as _pkg

    history = _pkg._volume_history.get(symbol)
    if not history or len(history) < 3:
        return None  # Not enough data yet

    # Calculate rolling average
    volumes = [h["volume"] for h in history]
    if not volumes:
        return None

    rolling_avg = sum(volumes) / len(volumes)
    if rolling_avg <= 0:
        return None

    ratio = current_volume / rolling_avg
    if ratio >= _pkg.VOLUME_SPIKE_THRESHOLD:
        severity = "critical"
    elif ratio >= _pkg.VOLUME_SPIKE_MILD_THRESHOLD:
        severity = "high"
    else:
        return None

    return {
        "symbol": symbol,
        "current_volume": current_volume,
        "rolling_avg_volume": round(rolling_avg, 1),
        "ratio": round(ratio, 2),
        "severity": severity,
    }

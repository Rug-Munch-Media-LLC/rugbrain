"""Dispatch social sentiment alerts via rugbrain.alert_dispatcher."""

from __future__ import annotations

from typing import Any


async def _dispatch_alert(alert: dict[str, Any]) -> None:
    """Dispatch a single alert via the alert_dispatcher if available."""
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        severity = alert.get("severity", "medium")
        title = alert["message"]
        await dispatch_alert(
            title=title,
            message=(
                f"Type: {alert['type']}\n"
                f"Symbol: {alert['symbol']}\n"
                f"Value: {alert.get('value', '?')}\n"
                f"Severity: {severity}\n"
                f"Source: LunarCrush Social Sentiment"
            ),
            alert_type=f"social_sentiment_{alert['type']}",
            severity=severity,
        )
    except Exception:
        pass  # Alert dispatcher not available or failed

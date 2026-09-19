"""Outbound webhook payload formatting."""

from __future__ import annotations

from typing import Any

from .models import CriticalAlert


def build_webhook_payload(alert: CriticalAlert) -> dict[str, Any]:
    """The JSON body community webhooks receive for a critical alert."""
    return {
        "event": "critical_alert",
        "id": alert.id,
        "title": alert.title,
        "severity": alert.severity,
        "chain": alert.chain,
        "source": alert.source,
        "amount_usd": alert.amount_usd,
        "description": alert.description,
        "addresses": alert.addresses,
        "tx_hash": alert.tx_hash,
        "url": alert.url,
        "detected_at": alert.detected_at,
    }

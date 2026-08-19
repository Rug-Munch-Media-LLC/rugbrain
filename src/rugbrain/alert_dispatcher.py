"""Alert Dispatcher — fires Telegram alerts for MEV, smart money, scam, and high-risk detections.

Central dispatch point that sends alerts to Telegram via the configured
ALERT_WEBHOOK_TELEGRAM. Called by MEV detector, smart money tracker,
YouTube scam detector, and newsletter scam detector.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Alert webhook URLs (from env)
WEBHOOKS = {
    "telegram": os.getenv("ALERT_WEBHOOK_TELEGRAM", ""),
    "slack": os.getenv("ALERT_WEBHOOK_SLACK", ""),
    "discord": os.getenv("ALERT_WEBHOOK_DISCORD", ""),
    "n8n": os.getenv("ALERT_WEBHOOK_N8N", ""),
}

MIN_SEVERITY = os.getenv("ALERT_MIN_SEVERITY", "high").lower()
SEVERITY_LEVELS = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
RATE_LIMIT_SECONDS = int(os.getenv("ALERT_RATE_LIMIT", "60"))
_last_alert: dict[str, float] = {}
_alert_semaphore = asyncio.Semaphore(5)


def _should_alert(severity: str) -> bool:
    """Check if severity meets the minimum threshold."""
    sev = SEVERITY_LEVELS.get(severity.lower(), 0)
    min_sev = SEVERITY_LEVELS.get(MIN_SEVERITY, 3)
    return sev >= min_sev


async def dispatch_alert(
    title: str,
    message: str,
    alert_type: str = "general",
    severity: str = "high",
    channels: list[str] | None = None,
) -> dict[str, bool]:
    """Dispatch an alert to configured channels."""
    if not _should_alert(severity):
        return {"sent": False, "reason": "below_threshold"}  # type: ignore[return-value]

    # Rate limit
    now = time.monotonic()
    rate_key = f"{alert_type}:{severity}"
    if rate_key in _last_alert and now - _last_alert[rate_key] < RATE_LIMIT_SECONDS:
        return {"sent": False, "reason": "rate_limited"}  # type: ignore[return-value]
    _last_alert[rate_key] = now

    if channels is None:
        channels = ["telegram"]

    results = {}
    async with _alert_semaphore:
        for channel in channels:
            url = WEBHOOKS.get(channel)
            if not url:
                results[channel] = False
                continue

            try:
                text = f"🚨 *{severity.upper()}*: {title}\n\n{message}"
                if channel == "telegram":
                    payload = {"text": text, "parse_mode": "Markdown"}
                elif channel == "slack":
                    payload = {
                        "text": title,
                        "mrkdwn": True,
                        "attachments": [
                            {
                                "text": message,
                                "color": "danger"
                                if severity in ("critical", "high")
                                else "warning",
                            }
                        ],
                    }
                elif channel == "discord":
                    color = (
                        0xFF0000
                        if severity == "critical"
                        else 0xFFA500
                        if severity == "high"
                        else 0xFFFF00
                    )
                    payload = {
                        "content": f"🚨 **{severity.upper()}**: {title}",
                        "embeds": [{"description": message, "color": color}],
                    }
                else:
                    payload = {"text": text}
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.post(url, json=payload)
                    results[channel] = r.status_code == 200
                    if r.status_code != 200:
                        logger.debug(
                            "alert_dispatch_failed channel=%s status=%d", channel, r.status_code
                        )
            except Exception as exc:
                logger.debug("alert_dispatch_error channel=%s error=%s", channel, str(exc)[:60])
                results[channel] = False

    logger.info(
        "alert_dispatched type=%s severity=%s channels=%s",
        alert_type,
        severity,
        list(results.keys()),
    )
    return results


async def alert_mev_sandwich(details: dict[str, Any]) -> dict[str, bool]:
    """Dispatch alert for detected MEV sandwich attack."""
    title = "MEV Sandwich Attack Detected"
    msg = (
        f"Frontrun: `{details.get('frontrun_tx', '?')[:20]}...`\n"
        f"Victim: `{details.get('victim_tx', '?')[:20]}...`\n"
        f"Backrun: `{details.get('backrun_tx', '?')[:20]}...`\n"
        f"Token Pair: {details.get('token_pair', '?')}\n"
        f"Severity: {details.get('severity', 'medium')}"
    )
    return await dispatch_alert(
        title, msg, alert_type="mev", severity=details.get("severity", "medium")
    )


async def alert_whale_movement(details: dict[str, Any]) -> dict[str, bool]:
    """Dispatch alert for significant whale movement."""
    title = f"Whale Movement: {details.get('value_eth', 0)} ETH"
    msg = (
        f"Wallet: `{details.get('wallet', '?')[:20]}...`\n"
        f"Chain: {details.get('chain', 'ethereum')}\n"
        f"Value: {details.get('value_eth', 0)} ETH\n"
        f"Tx: `{details.get('tx_hash', '?')[:20]}...`\n"
        f"Severity: {details.get('severity', 'medium')}"
    )
    return await dispatch_alert(
        title, msg, alert_type="whale", severity=details.get("severity", "medium")
    )


async def alert_scam_detection(details: dict[str, Any]) -> dict[str, bool]:
    """Dispatch alert for scam detection from YouTube/podcast/newsletter."""
    title = f"Scam Signal: {details.get('channel', 'Unknown')}"
    msg = (
        f"Source: {details.get('source', '?')}\n"
        f"Channel: {details.get('channel', '?')}\n"
        f"Title: {details.get('title', '?')[:60]}\n"
        f"Risk Score: {details.get('risk_score', 0)}\n"
        f"Coins: {', '.join(details.get('coins', [])[:5])}\n"
        f"Keywords: {', '.join(details.get('scam_keywords', [])[:3])}"
    )
    return await dispatch_alert(title, msg, alert_type="scam", severity="high")


async def alert_newsletter_high_risk(details: dict[str, Any]) -> dict[str, bool]:
    """Dispatch alert for high-risk newsletter content."""
    title = f"High-Risk Newsletter: {details.get('name', 'Unknown')}"
    msg = (
        f"Newsletter: {details.get('name', '?')}\n"
        f"URL: {details.get('url', '?')[:60]}...\n"
        f"Coins Mentioned: {', '.join(details.get('coins', [])[:5])}\n"
        f"Category: {details.get('category', '?')}"
    )
    return await dispatch_alert(title, msg, alert_type="newsletter", severity="high")


async def rerank_alert_priority(
    alerts: list[dict[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Use Qwen3 reranker to score alert priority.

    Args:
        alerts: List of alert dicts with 'title' and 'message' keys.
        top_k: Return top-k ranked alerts.

    Returns alerts sorted by threat score descending.
    Degrades gracefully: returns top_k unsorted on failure.
    """
    try:
        from rugbrain.reranker import rerank_alerts

        return await rerank_alerts(alerts, top_k=top_k)
    except Exception:
        return alerts[:top_k]

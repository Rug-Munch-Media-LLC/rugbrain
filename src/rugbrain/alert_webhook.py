"""Alert Webhook — push notifications for critical findings.

Sends alerts to configured webhooks (Telegram, Slack, Discord, n8n) when
the RAG system detects a critical finding. Supports multiple channels
with rate limiting to avoid flooding.

Configuration via env vars:
    ``ALERT_WEBHOOK_TELEGRAM`` — Telegram bot API URL
    ``ALERT_WEBHOOK_SLACK`` — Slack webhook URL
    ``ALERT_WEBHOOK_DISCORD`` — Discord webhook URL
    ``ALERT_WEBHOOK_N8N`` — n8n webhook URL
    ``ALERT_MIN_SEVERITY`` — minimum severity to alert on (default: "high")
    ``ALERT_RATE_LIMIT`` — seconds between alerts per channel (default: 60)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Channel config from env vars
CHANNELS: dict[str, str | None] = {
    "telegram": os.getenv("ALERT_WEBHOOK_TELEGRAM"),
    "slack": os.getenv("ALERT_WEBHOOK_SLACK"),
    "discord": os.getenv("ALERT_WEBHOOK_DISCORD"),
    "n8n": os.getenv("ALERT_WEBHOOK_N8N"),
}

# Filter configuration
MIN_SEVERITY = os.getenv("ALERT_MIN_SEVERITY", "high").lower()
RATE_LIMIT_SECONDS = int(os.getenv("ALERT_RATE_LIMIT", "60"))

# Severity levels
SEVERITY_LEVELS = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# Rate limiting: {channel_name: last_alert_time}
_last_alert: dict[str, float] = {}

# Semaphore to avoid flooding
_alert_semaphore = asyncio.Semaphore(5)


def _severity_meets_threshold(severity: str) -> bool:
    """Check if severity meets the minimum threshold."""
    sev_level = SEVERITY_LEVELS.get(severity.lower(), 0)
    min_level = SEVERITY_LEVELS.get(MIN_SEVERITY, 3)
    return sev_level >= min_level


def _format_alert_message(finding: dict[str, Any]) -> str:
    """Format a finding into a concise alert message."""
    risk_level = finding.get("risk_level", "unknown").upper()
    name = finding.get("name", "Unknown")
    symbol = finding.get("symbol", "???")
    chain = finding.get("chain", "unknown")
    address = finding.get("token_address", "unknown")
    safety = finding.get("safety_score", 0)
    flags = finding.get("risk_flags", [])

    lines = [
        f"🚨 *{risk_level} ALERT*: {name} ({symbol})",
        f"Chain: {chain}",
        f"Safety: {safety}/100",
        f"Address: `{address[:20]}...`",
    ]
    if flags:
        lines.append(f"Flags: {', '.join(flags[:8])}")
    return "\n".join(lines)


async def send_alert(
    finding: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    """Send an alert about a critical finding to all configured channels.

    Args:
        finding: The finding dict (same format as scanner_subscriber payload).
        force: If True, skip rate limiting and severity checks.

    Returns:
        Dict with ``sent`` (list of channels sent to), ``skipped`` (list with
        reason), and ``errors`` (list with error messages).
    """
    severity = finding.get("risk_level", "low")
    if not force and not _severity_meets_threshold(severity):
        return {"sent": [], "skipped": [{"channel": "all", "reason": f"severity {severity} below threshold {MIN_SEVERITY}"}], "errors": []}

    now = time.time()
    message = _format_alert_message(finding)

    results: dict[str, list[str]] = {"sent": [], "skipped": [], "errors": []}

    async with _alert_semaphore:
        for channel_name, webhook_url in CHANNELS.items():
            if not webhook_url:
                continue

            # Rate limiting
            last = _last_alert.get(channel_name, 0)
            if not force and (now - last) < RATE_LIMIT_SECONDS:
                results["skipped"].append(
                    {"channel": channel_name, "reason": "rate_limited"}
                )
                continue

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    if "discord.com" in webhook_url:
                        payload = {"content": message}
                    elif "slack.com" in webhook_url:
                        payload = {"text": message}
                    elif "n8n" in webhook_url or "n8n" in channel_name:
                        payload = finding
                    else:
                        # Telegram / generic
                        payload = {"text": message, "parse_mode": "Markdown"}

                    r = await client.post(webhook_url, json=payload)
                    if r.status_code in (200, 201, 204):
                        _last_alert[channel_name] = now
                        results["sent"].append(channel_name)
                    else:
                        results["errors"].append(
                            {"channel": channel_name, "status": r.status_code}
                        )
            except Exception as exc:
                results["errors"].append(
                    {"channel": channel_name, "error": str(exc)[:100]}
                )

    return results


async def send_alert_batch(
    findings: list[dict[str, Any]],
    force: bool = False,
) -> list[dict[str, Any]]:
    """Send alerts for a batch of findings."""
    results = []
    for finding in findings:
        result = await send_alert(finding, force=force)
        results.append(result)
        await asyncio.sleep(1)  # Brief delay between individual alerts
    return results

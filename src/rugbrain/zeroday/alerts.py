"""Telegram alerts for critical 0-days/exploits and high-value bounties.

Reuses the fleet webhook convention (``ALERT_WEBHOOK_TELEGRAM``) and the
existing alert dispatcher's channel, but formats zeroday records. No webhook
configured means dry-run: the gate is evaluated and reported, nothing is sent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugbrain.zeroday.pipeline import Change
from rugbrain.zeroday.schema import SEVERITY_LEVEL

log = logging.getLogger(__name__)

ALERT_MIN_SEVERITY = os.getenv("ZERODAY_ALERT_MIN_SEVERITY", "high").lower()
BOUNTY_MIN_USD = float(os.getenv("ZERODAY_BOUNTY_MIN_USD", "100000"))
MAX_MESSAGE = 3800


def webhook_url() -> str | None:
    """Telegram bot API URL, or None (dry-run)."""
    return os.getenv("ALERT_WEBHOOK_TELEGRAM", "").strip() or None


def should_alert(change: Change) -> bool:
    """Critical 0-day / exploit, or a bounty at/above the value floor."""
    record = change.record
    if change.event == "updated" and record.kind in ("0day", "exploit"):
        return False  # a re-worded advisory is not a new incident
    if record.kind in ("0day", "exploit"):
        return SEVERITY_LEVEL.get(record.severity, -1) >= SEVERITY_LEVEL[ALERT_MIN_SEVERITY]
    if record.kind in ("bounty", "contest"):
        return record.reward_usd is not None and record.reward_usd >= BOUNTY_MIN_USD
    return False


def _money(value: float | None) -> str | None:
    return f"${value:,.0f}" if value is not None else None


def build_message(change: Change) -> str:
    """Compact Telegram message with the canonical link first."""
    record = change.record
    lines = [f"🚨 {record.severity.upper()} {record.kind.upper()}", record.title]
    if record.protocol:
        lines.append(f"protocol: {record.protocol}")
    if record.chain:
        lines.append(f"chain: {record.chain}")
    if (loss := _money(record.loss_usd)) is not None:
        lines.append(f"loss: {loss}")
    if (reward := _money(record.reward_usd)) is not None:
        lines.append(f"reward: {reward}")
    if record.tx_ref:
        lines.append(f"tx: {record.tx_ref}")
    if record.fixed_version:
        lines.append(f"fixed: {record.fixed_version}")
    lines.append(f"source: {record.source} · confidence: {record.confidence}")
    lines.append(record.url)
    text = "\n".join(lines)
    return text[:MAX_MESSAGE]


def _alerted(state: dict[str, Any], change: Change) -> bool:
    seen = state.get("alerts", {}).get(change.cluster_id)
    return bool(seen) and seen == change.record.fingerprint()


def _mark(state: dict[str, Any], change: Change) -> None:
    state.setdefault("alerts", {})[change.cluster_id] = change.record.fingerprint()


async def send_batch(
    changes: list[Change],
    state: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Send at most one alert per cluster+fingerprint; returns sent cluster ids."""
    url = webhook_url()
    pending = [c for c in changes if should_alert(c) and not _alerted(state, c)]
    if not pending:
        return []
    if url is None:
        log.info("zeroday_alerts_dry_run pending=%d", len(pending))
        return []
    sent: list[str] = []
    owned = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        for change in pending:
            try:
                response = await http.post(url, json={"text": build_message(change)})
                response.raise_for_status()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("zeroday_alert_failed id=%s err=%s", change.cluster_id, str(exc)[:120])
                continue
            _mark(state, change)
            sent.append(change.cluster_id)
    finally:
        if owned:
            await http.aclose()
    return sent


__all__ = [
    "ALERT_MIN_SEVERITY",
    "BOUNTY_MIN_USD",
    "build_message",
    "send_batch",
    "should_alert",
    "webhook_url",
]

"""Telegram critical-alert delivery (direct Bot API, bot-queue fallback)."""

from __future__ import annotations

import json
import logging

import httpx

from .config import TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from .models import CriticalAlert

log = logging.getLogger("rugbrain.realtime_alerts")


async def _send_telegram(alert: CriticalAlert) -> bool:
    """Push the exploit to the configured Telegram channel/group."""
    text = (
        f"\U0001f6a8 CRITICAL EXPLOIT ALERT\n"
        f"{alert.title}\n"
        f"Chain: {alert.chain} | Source: {alert.source}\n"
        f"Amount: ${alert.amount_usd:,.0f}\n"
        f"{alert.description[:600]}"
    )
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": text[:4000],
                        "disable_web_page_preview": True,
                    },
                )
                ok = r.status_code == 200
                if not ok:
                    log.warning("telegram_send_failed status=%d", r.status_code)
                return ok
        except Exception as e:
            log.warning("telegram_send_error: %s", e)
            return False
    # Fallback: enqueue for the existing bot (drained by rmi-backend).
    from rugbrain import realtime_alerts as _pkg

    try:
        redis_client = await _pkg._get_redis()
        if redis_client is not None:
            await redis_client.lpush(
                "rag:telegram_queue", json.dumps({"text": text[:4000], "global": True})
            )
            return True
    except Exception as e:
        log.debug("telegram_queue_failed: %s", e)
    return False

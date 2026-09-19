"""DeFiLlama hacks-database poller."""

from __future__ import annotations

import logging

import httpx

from . import config
from .dates import _ts_to_iso, _within_window
from .models import SecurityAlert
from .vectors import detect_vector

log = logging.getLogger("rugbrain.alerts")


async def poll_defillama(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Major exploits from the DeFiLlama hacks database (recent window)."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(config.DEFILLAMA_HACKS_URL)
        if r.status_code != 200:
            log.warning("defillama_poll_failed status=%d", r.status_code)
            return alerts
        for hack in r.json() or []:
            if not isinstance(hack, dict):
                continue
            ts = float(hack.get("date") or 0)
            if ts <= 0:
                continue
            date_iso = _ts_to_iso(ts)
            if not _within_window(date_iso, now, config.RECENT_WINDOW_DAYS):
                continue
            name = str(hack.get("name", "")).strip() or "unknown"
            amount = float(hack.get("amount") or 0)
            chains = hack.get("chain") or []
            chain = str(chains[0]).lower() if isinstance(chains, list) and chains else "unknown"
            technique = str(hack.get("technique") or "").strip()
            classification = str(hack.get("classification") or "").strip()
            vector = detect_vector(f"{technique} {classification} {name}")
            description = (
                f"{name} was exploited for ${amount:,.0f} "
                f"({technique or classification or 'unknown technique'}). "
                f"Target type: {hack.get('targetType') or 'unknown'}. "
                f"Language: {hack.get('language') or 'unknown'}."
            )
            alerts.append(
                SecurityAlert(
                    source="defillama",
                    title=f"Exploit: {name} (${amount:,.0f})",
                    description=description,
                    severity="critical" if amount >= 10_000_000 else "high",
                    attack_vector=vector,
                    chain=chain,
                    protocol=name,
                    amount_lost_usd=amount,
                    date=date_iso,
                    raw={
                        "technique": technique,
                        "classification": classification,
                        "returned_funds": hack.get("returnedFunds"),
                    },
                )
            )
    except Exception as e:
        log.warning("defillama_poll_error: %s", e)
    return alerts

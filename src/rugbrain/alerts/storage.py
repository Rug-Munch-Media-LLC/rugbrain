"""Qdrant storage for security alerts."""

from __future__ import annotations

import logging

import httpx

from . import config
from .models import SecurityAlert

log = logging.getLogger("rugbrain.alerts")


async def _ensure_alert_collection(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"/collections/{config.ALERT_COLLECTION}")
        if r.status_code == 200:
            return True
    except Exception as e:
        log.debug("alert_collection_probe_failed: %s", e)
    try:
        r = await client.put(
            f"/collections/{config.ALERT_COLLECTION}",
            json={"vectors": {"size": config.EMBEDDING_DIM, "distance": "Cosine"}},
        )
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning("alert_collection_create_failed: %s", e)
        return False


async def store_alert(alert: SecurityAlert, embedding: list[float]) -> bool:
    """Upsert *alert* into the Qdrant ``security_alerts`` collection."""
    try:
        async with httpx.AsyncClient(base_url=config.QDRANT_URL, timeout=30.0) as client:
            if not await _ensure_alert_collection(client):
                return False
            payload = {
                "points": [
                    {
                        "id": alert.id,
                        "vector": embedding,
                        "payload": {
                            "content": alert.text()[:10000],
                            "metadata": {
                                "source": alert.source,
                                "title": alert.title,
                                "protocol": alert.protocol,
                                "attack_vector": alert.attack_vector,
                                "chain": alert.chain,
                                "amount_lost_usd": alert.amount_lost_usd,
                                "date": alert.date,
                                "detected_at": alert.detected_at,
                                "url": alert.url,
                                "severity": alert.severity,
                            },
                            "source": alert.source,
                            "severity": alert.severity,
                            "chain": alert.chain,
                            "collection": config.ALERT_COLLECTION,
                            "source_type": "alert",
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{config.ALERT_COLLECTION}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                log.warning("alert_store_failed status=%d body=%s", r.status_code, r.text[:200])
            return ok
    except Exception as e:
        log.warning("alert_store_error: %s", e)
        return False

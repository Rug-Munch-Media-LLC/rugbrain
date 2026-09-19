"""Qdrant ``live_threats`` upsert + scroll (zero-vector-safe)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.dim_policy import normalize_dims

from .config import (
    EMBEDDING_DIM,
    LIVE_THREATS_COLLECTION,
    QDRANT_URL,
    QWEN_EMBED_MODEL_NAME,
    QWEN_EMBED_URL,
)
from .models import CriticalAlert
from .payloads import build_webhook_payload

log = logging.getLogger("rugbrain.realtime_alerts")


async def _embed_critical(text: str) -> list[float] | None:
    """Embed through Qwen3-4B (L2-normalized). None on failure."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{QWEN_EMBED_URL}/embeddings",
                json={"model": QWEN_EMBED_MODEL_NAME, "input": [text[:8000]]},
            )
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            vec = data[0].get("embedding", [])
            if not isinstance(vec, list) or not vec:
                return None
            # Central dim policy: MRL-truncate oversize + L2 renormalize;
            # undersize raises (no silent truncation of short vectors).
            return normalize_dims([float(x) for x in vec], EMBEDDING_DIM)
    except Exception as e:
        log.debug("live_threat_embed_failed: %s", e)
        return None


async def _ensure_live_collection(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"/collections/{LIVE_THREATS_COLLECTION}")
        if r.status_code == 200:
            return True
    except Exception as e:
        log.debug("live_collection_probe_failed: %s", e)
    try:
        r = await client.put(
            f"/collections/{LIVE_THREATS_COLLECTION}",
            json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
        )
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning("live_collection_create_failed: %s", e)
        return False


async def store_qdrant_live_threat(alert: CriticalAlert) -> bool:
    """Upsert *alert* into Qdrant ``live_threats`` (embed-gated, no zero vectors)."""
    try:
        embedding = await _embed_critical(alert.text())
        if not embedding:
            # 2026-08-25 quality fix: do NOT store zero vectors — a [0.0]*N
            # vector has no direction, so cosine similarity against it is
            # undefined and poisons nearest-neighbor ranking for every real
            # query. The alert still flows through ClickHouse/Telegram lanes;
            # only the vector index skips this point.
            log.warning(
                "live_threat_skipped_no_embedding id=%s source=%s (embedder down; "
                "point NOT indexed to avoid zero-vector poisoning)",
                alert.id,
                alert.source,
            )
            return False
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30.0) as client:
            if not await _ensure_live_collection(client):
                return False
            payload = {
                "points": [
                    {
                        "id": alert.id,
                        "vector": embedding,
                        "payload": {
                            "content": alert.text()[:10000],
                            "metadata": build_webhook_payload(alert),
                            "source": alert.source,
                            "severity": alert.severity,
                            "chain": alert.chain,
                            "collection": LIVE_THREATS_COLLECTION,
                            "source_type": "critical_alert",
                            "status": alert.status,
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{LIVE_THREATS_COLLECTION}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                log.warning("live_threat_store_failed status=%d", r.status_code)
            return ok
    except Exception as e:
        log.warning("live_threat_store_error: %s", e)
        return False


async def scroll_live_threats(limit: int = 100) -> list[dict[str, Any]]:
    """Read active threats from Qdrant ``live_threats`` (empty on failure)."""
    try:
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=15.0) as client:
            if not await _ensure_live_collection(client):
                return []
            r = await client.post(
                f"/collections/{LIVE_THREATS_COLLECTION}/points/scroll",
                json={"limit": limit, "with_payload": True, "with_vector": False},
            )
            if r.status_code != 200:
                return []
            points = (r.json().get("result", {}) or {}).get("points", []) or []
            out = []
            for p in points:
                payload = (p.get("payload") or {}).get("metadata") or {}
                payload.setdefault("id", p.get("id"))
                payload.setdefault("detected_at", "")
                payload.setdefault("status", "active")
                out.append(payload)
            return out
    except Exception as e:
        log.debug("live_threats_scroll_failed: %s", e)
        return []

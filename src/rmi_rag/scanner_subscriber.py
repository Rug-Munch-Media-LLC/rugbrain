"""
Scanner Subscriber — live ingestion of high-risk scan findings into Qdrant.

Listens on Redis channel ``rmi:rag:ingest`` for JSON payloads published by
rmi-backend's ``UnifiedTokenScanner`` after every high/critical-risk scan.
On receipt: generates a 2560-dim embedding (via the existing embedding pipeline)
and upserts into the Qdrant ``threat_intel`` collection for live RAG search.

Architecture::
    Scanner (rmi-backend)
        → Redis pub/sub ``rmi:rag:ingest``
        → Subscriber (rmi-rag, background task)
        → ``get_embedding()`` → ``QdrantVectorStore.insert()`` → Qdrant ``threat_intel``

If Redis is down the subscriber retries every 5s. Scanner findings are still
stored in Postgres (existing behavior) — no data loss.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# ── Subscriber state ───────────────────────────────────────────────────────
_subscriber_running = False
_subscriber_connected = False  # set when Redis subscription actually succeeds
_subscriber_task: asyncio.Task | None = None
_ingested_count: int = 0
_last_ingested_at: str = ""


def is_subscriber_running() -> bool:
    """Return whether the subscriber background task is alive."""
    global _subscriber_running
    return _subscriber_running


def subscriber_stats() -> dict[str, Any]:
    """Return subscriber statistics for health/observability."""
    global _ingested_count, _last_ingested_at
    return {
        "running": _subscriber_running,
        "connected": _subscriber_connected,
        "ingested_count": _ingested_count,
        "last_ingested_at": _last_ingested_at,
        "channel": "rmi:rag:ingest",
    }


async def start_subscriber() -> None:
    """Start the Redis pub/sub listener as a background asyncio task.

    Called during rmi-rag FastAPI lifespan startup. Spawns a single
    long-lived coroutine that reconnects on failure.
    """
    global _subscriber_running, _subscriber_task

    if _subscriber_running:
        logger.warning("scanner_subscriber already running — skipping")
        return

    _subscriber_task = asyncio.create_task(_listen_forever())
    # Give it a moment to connect so we can report accurate startup status
    await asyncio.sleep(0.1)
    _subscriber_running = True
    logger.info("scanner_subscriber started — listening on rmi:rag:ingest")


async def stop_subscriber() -> None:
    """Gracefully stop the subscriber background task."""
    global _subscriber_running, _subscriber_task
    _subscriber_running = False
    if _subscriber_task is not None:
        _subscriber_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _subscriber_task
        _subscriber_task = None
    logger.info("scanner_subscriber stopped")


# ── Core listener loop ─────────────────────────────────────────────────────


async def _listen_forever() -> None:
    """Connect to Redis, subscribe to ``rmi:rag:ingest``, process messages.

    Runs until cancelled. Reconnects on Redis failure after a 5-second delay.
    """
    import redis.asyncio as aioredis

    while True:
        r: aioredis.Redis | None = None
        try:
            r = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD or None,
                decode_responses=True,
            )
            pubsub = r.pubsub()
            await pubsub.subscribe("rmi:rag:ingest")

            # Verify subscription actually worked
            channels = await r.pubsub_channels()
            if b"rmi:rag:ingest" in channels or "rmi:rag:ingest" in channels:
                global _subscriber_connected
                _subscriber_connected = True

            logger.info(
                "subscriber_connected host=%s port=%s channel=rmi:rag:ingest",
                REDIS_HOST,
                REDIS_PORT,
            )

            async for message in pubsub.listen():
                if not _subscriber_running:
                    logger.info("subscriber_stopping")
                    return

                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except json.JSONDecodeError:
                    logger.warning(
                        "subscriber_invalid_json data=%s", str(message.get("data", ""))[:200]
                    )
                    continue

                try:
                    await _process_finding(data)
                except Exception as exc:
                    logger.error(
                        "subscriber_process_failed error=%s",
                        str(exc)[:200],
                        exc_info=True,
                    )

        except asyncio.CancelledError:
            _subscriber_connected = False
            logger.info("subscriber_cancelled")
            return
        except Exception as exc:
            _subscriber_connected = False
            logger.warning(
                "subscriber_connection_error error=%s",
                str(exc)[:200],
            )
            await asyncio.sleep(5)
        finally:
            if r is not None:
                with contextlib.suppress(Exception):
                    await r.close()


# ── Message processing ─────────────────────────────────────────────────────


async def _process_finding(data: dict[str, Any]) -> None:
    """Generate an embedding for a scan finding and upsert into Qdrant.

    Steps:
      1. Build a descriptive text from the finding payload.
      2. Generate a 2560-dim embedding via ``get_embedding()``.
      3. Upsert into Qdrant ``threat_intel`` collection via ``QdrantVectorStore.insert()``.
    """
    global _ingested_count, _last_ingested_at

    token_address = data.get("token_address", "unknown")
    chain = data.get("chain", "unknown")
    risk_level = data.get("risk_level", "unknown")
    safety_score = data.get("safety_score", 0)
    name = data.get("name", "")
    symbol = data.get("symbol", "")
    risk_flags: list[str] = data.get("risk_flags", [])

    # Build descriptive text for embedding
    content_lines = [
        f"SCAM ALERT [{chain.upper()}] {name} ({symbol}) - {token_address}",
        f"Risk Level: {risk_level.upper()} | Safety Score: {safety_score}/100",
    ]
    if risk_flags:
        content_lines.append(f"Risk Flags: {', '.join(risk_flags[:15])}")
    if data.get("price_usd"):
        content_lines.append(f"Price: ${data['price_usd']:.6f}")
    if data.get("liquidity_usd"):
        content_lines.append(f"Liquidity: ${data['liquidity_usd']:.2f}")
    if data.get("age_hours"):
        content_lines.append(f"Age: {data['age_hours']:.1f} hours")
    if data.get("scanned_at"):
        content_lines.append(f"Scanned at: {data['scanned_at']}")

    content = "\n".join(content_lines)

    # Generate embedding (uses same pipeline as rest of rmi-rag)
    from rmi_rag.embeddings import get_embedding

    try:
        embedding = await get_embedding(content)
    except Exception as exc:
        logger.error(
            "embedding_failed token=%s error=%s",
            token_address,
            str(exc)[:120],
        )
        return

    # Upsert into Qdrant threat_intel collection
    from rmi_rag.qdrant_store import QdrantVectorStore

    doc_id = f"scanner_{chain}_{token_address}".replace(":", "_").lower()

    store = QdrantVectorStore()
    ok = await store.insert(
        doc_id=doc_id,
        collection="threat_intel",
        embedding=embedding,
        content=content,
        metadata={
            "token_address": token_address,
            "chain": chain,
            "name": name,
            "symbol": symbol,
            "safety_score": safety_score,
            "risk_level": risk_level,
            "risk_flags": risk_flags[:20],
            "scanned_at": data.get("scanned_at", ""),
            "source": "scanner",
        },
        source="scanner",
        severity=risk_level,
        chain=chain,
    )

    if ok:
        _ingested_count += 1
        _last_ingested_at = data.get("scanned_at", "")
        logger.info(
            "threat_intel_upserted token=%s chain=%s risk=%s doc_id=%s",
            token_address,
            chain,
            risk_level,
            doc_id,
        )
    else:
        logger.warning(
            "threat_intel_upsert_failed token=%s chain=%s",
            token_address,
            chain,
        )

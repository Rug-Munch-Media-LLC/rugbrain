"""
Scanner Poller — polling-based fallback for scanner->RAG ingestion.

DragonflyDB pub/sub is unreliable in certain versions (v1.39.x known issue)
and BLPop blocking commands also have compatibility issues with Dragonfly.
This module uses LPOP in a polling loop (non-blocking) to read from
a Redis list (``rmi:rag:findings_queue``).

Architecture::
    Scanner (rmi-backend)
        → Redis LPUSH ``rmi:rag:findings_queue``
        → Poller (rugbrain, background task, LPOP polling loop)
        → ``_process_finding()`` (shared with scanner_subscriber)
        → ``QdrantVectorStore.insert()`` → Qdrant ``threat_intel_v2``

Usage::
    from rugbrain.scanner_subscriber import is_subscriber_running, start_polling
    await start_polling()  # starts the LPOP-based polling fallback
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
QUEUE_KEY = "rmi:rag:findings_queue"
POLL_TIMEOUT = 30  # Kept for backward compatibility; LPOP uses 1s sleep between polls

# ── Poller state ───────────────────────────────────────────────────────────
_poller_running = False
_poller_task: asyncio.Task[Any] | None = None
_poller_ingested_count: int = 0
_poller_last_ingested_at: str = ""


def is_poller_running() -> bool:
    """Return whether the poller background task is alive."""
    global _poller_running
    return _poller_running


def poller_stats() -> dict[str, Any]:
    """Return poller statistics for health/observability."""
    global _poller_ingested_count, _poller_last_ingested_at
    return {
        "running": _poller_running,
        "ingested_count": _poller_ingested_count,
        "last_ingested_at": _poller_last_ingested_at,
        "queue": QUEUE_KEY,
        "mode": "polling",
    }


async def start_poller() -> None:
    """Start the polling fallback as a background asyncio task.

    Called during rugbrain FastAPI lifespan startup if the pub/sub subscriber
    cannot connect or as a secondary delivery mechanism.
    """
    global _poller_running, _poller_task

    if _poller_running:
        logger.warning("scanner_poller already running — skipping")
        return

    _poller_task = asyncio.create_task(_poll_forever())
    await asyncio.sleep(0.1)
    _poller_running = True
    logger.info("scanner_poller started — polling queue=%s", QUEUE_KEY)


async def stop_poller() -> None:
    """Gracefully stop the poller background task."""
    global _poller_running, _poller_task
    _poller_running = False
    if _poller_task is not None:
        _poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _poller_task
        _poller_task = None
    logger.info("scanner_poller stopped")


# ── Core polling loop ──────────────────────────────────────────────────────


async def _poll_forever() -> None:
    """LPoll from the findings queue and process each item.

    Uses LPOP in a polling loop (non-blocking) because DragonflyDB has
    known issues with BLPop blocking commands (v1.39.x+). This approach
    is slightly less efficient but reliable across all Redis-compatible
    backends.
    """
    import redis.asyncio as aioredis

    while True:
        r: aioredis.Redis[str] | None = None
        try:
            r = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                retry_on_timeout=True,
            )
            await r.ping()
            logger.info(
                "poller_connected host=%s port=%s queue=%s",
                REDIS_HOST,
                REDIS_PORT,
                QUEUE_KEY,
            )

            while _poller_running:
                try:
                    # LPOP is non-blocking — safe with DragonflyDB
                    # Sleep 1s between empty polls (check _poller_running)
                    value = await r.lpop(QUEUE_KEY)
                    if value is None:
                        await asyncio.sleep(1)
                        continue

                    try:
                        data = json.loads(value)
                    except json.JSONDecodeError:
                        logger.warning("poller_invalid_json data=%s", str(value)[:200])
                        continue

                    await _process_poller_finding(data)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("poller_iteration_error error=%s", str(exc)[:200])
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("poller_cancelled")
            return
        except Exception as exc:
            logger.warning("poller_connection_error error=%s", str(exc)[:200])
            await asyncio.sleep(5)
        finally:
            if r is not None:
                with contextlib.suppress(Exception):
                    await r.close()


# ── Message processing ─────────────────────────────────────────────────────


async def _process_poller_finding(data: dict[str, Any]) -> None:
    """Generate an embedding for a scan finding and upsert into Qdrant.

    Mirrors ``scanner_subscriber._process_finding()`` with shared logic.
    """
    global _poller_ingested_count, _poller_last_ingested_at

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

    # Generate embedding (uses same pipeline as rest of rugbrain)
    from rugbrain.embeddings import get_embedding

    try:
        embedding = await get_embedding(content)
    except Exception as exc:
        logger.error(
            "poller_embedding_failed token=%s error=%s",
            token_address,
            str(exc)[:120],
        )
        return

    # Upsert into Qdrant threat_intel_v2 collection
    from rugbrain.qdrant_store import QdrantVectorStore

    doc_id = f"scanner_{chain}_{token_address}".replace(":", "_").lower()

    store = QdrantVectorStore()
    ok = await store.insert(
        doc_id=doc_id,
        collection="threat_intel_v2",
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
        _poller_ingested_count += 1
        _poller_last_ingested_at = data.get("scanned_at", "")
        logger.info(
            "poller_threat_intel_upserted token=%s chain=%s risk=%s doc_id=%s",
            token_address,
            chain,
            risk_level,
            doc_id,
        )
    else:
        logger.warning(
            "poller_threat_intel_upsert_failed token=%s chain=%s",
            token_address,
            chain,
        )

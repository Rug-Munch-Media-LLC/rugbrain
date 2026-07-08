"""
Streaming RAG - Real-time token/address monitoring with push updates.

"Watch this token" → results stream as the scanner finds new evidence.
Uses Redis pub/sub for real-time delivery to WebSocket or polling clients.

Architecture:
  1. Client POSTs /rag/watch with token address + filters
  2. Server subscribes to scanner output channel
  3. As new scans complete, results are pushed via:
     - WebSocket (existing /ws/ endpoint)
     - Redis pub/sub (for external clients)
  4. Client polls /rag/watch/{id}/updates for new results
"""

import asyncio
import contextlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory watch registry ─────────────────────────────────────
# In production, this would use Redis for persistence across restarts
_watches: dict[str, dict[str, Any]] = {}
_updates: dict[str, list[dict[str, Any]]] = {}
_background_tasks: set[asyncio.Task] = set()


async def create_watch(
    token_address: str,
    chain: str = "ethereum",
    filters: list[str] | None = None,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    """
    Create a new watch on a token/address.

    Returns watch_id for polling or WebSocket subscription.
    """
    watch_id = str(uuid.uuid4())[:12]

    watch = {
        "id": watch_id,
        "token_address": token_address,
        "chain": chain,
        "filters": filters or ["scam_detection", "deployer_analysis", "liquidity_check"],
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC).timestamp() + ttl_seconds),
        "status": "active",
        "total_updates": 0,
    }

    _watches[watch_id] = watch
    _updates[watch_id] = []

    logger.info("Created watch %s for %s... on %s", watch_id, token_address[:12], chain)

    # Kick off async scanning
    task = asyncio.create_task(_run_watch_scans(watch_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return watch


async def get_watch_updates(watch_id: str, since_index: int = 0) -> dict[str, Any]:
    """Get new updates for a watch since the given index."""
    watch = _watches.get(watch_id)
    if not watch:
        return {"error": "Watch not found", "watch_id": watch_id}

    all_updates = _updates.get(watch_id, [])
    new_updates = all_updates[since_index:]

    return {
        "watch_id": watch_id,
        "status": watch["status"],
        "new_updates": new_updates,
        "total_updates": len(all_updates),
        "next_index": len(all_updates),
    }


async def _run_watch_scans(watch_id: str):
    """Run scanner checks for a watch and push results."""
    watch = _watches.get(watch_id)
    if not watch:
        return

    token_address = watch["token_address"]
    chain = watch["chain"]
    watch.get("filters", [])

    try:
        # 1. Run unified scanner if available
        try:
            from app.domains.scanners.unified_scanner import scan_wallet  # external dependency

            result = await scan_wallet(token_address, chain=chain)
            _push_update(
                watch_id,
                {
                    "type": "scanner",
                    "source": "unified_scanner",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "risk_score": result.get("risk_score", 0),
                    "risk_level": result.get("risk_level", "unknown"),
                    "issues": result.get("issues", []),
                    "warnings": result.get("warnings", []),
                },
            )
        except ImportError:
            logger.debug("Unified scanner not available")
        except Exception as e:
            logger.debug("Scanner failed: %s", e)

        # 2. RAG search for known scam patterns
        try:
            from rmi_rag.search_service import three_pillar_search

            rag_results = await three_pillar_search(
                query=f"{token_address} scam detection",
                collections=["known_scams", "scam_patterns", "forensic_reports"],
                limit=5,
                use_mmr=True,
            )
            if rag_results.get("results"):
                _push_update(
                    watch_id,
                    {
                        "type": "rag",
                        "source": "three_pillar_search",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "matches": len(rag_results["results"]),
                        "top_match": rag_results["results"][0].get("content", "")[:200],
                        "confidence": rag_results.get("confidence"),
                    },
                )
        except ImportError:
            logger.debug("RAG search not available for streaming")
        except Exception as e:
            logger.debug("RAG search failed in watch: %s", e)

        # 3. Deployer reputation
        try:
            from app.deployer_reputation import score_deployer_risk  # external dependency

            reputation = score_deployer_risk(
                address=token_address,
                deployment_count=0,
            )
            _push_update(
                watch_id,
                {
                    "type": "reputation",
                    "source": "deployer_reputation",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "risk_score": reputation.get("risk_score", 0),
                    "risk_label": reputation.get("risk_label", "unknown"),
                    "summary": reputation.get("summary", ""),
                },
            )
        except ImportError:
            logger.debug("Deployer reputation not available")
        except Exception as e:
            logger.debug("Reputation failed: %s", e)

    finally:
        watch["status"] = "completed"
        watch["completed_at"] = datetime.now(UTC).isoformat()

    # Publish to Redis for WebSocket clients
    with contextlib.suppress(Exception):
        await _publish_to_redis(watch_id, watch)


def _push_update(watch_id: str, update: dict[str, Any]):
    """Push an update to the in-memory queue."""
    if watch_id not in _updates:
        _updates[watch_id] = []
    _updates[watch_id].append(update)
    if watch_id in _watches:
        _watches[watch_id]["total_updates"] = len(_updates[watch_id])


async def _publish_to_redis(watch_id: str, watch: dict[str, Any]):
    """Publish watch completion to Redis for WebSocket delivery."""
    try:
        import redis.asyncio as aioredis

        redis_host = os.getenv("REDIS_HOST", "rmi-redis")
        redis_pass = os.getenv("REDIS_PASSWORD", "")
        r = await aioredis.from_url(
            f"redis://{redis_host}:{os.getenv('REDIS_PORT', '6379')}",
            password=redis_pass or None,
        )
        await r.publish(
            "rmi:ws:alerts",
            json.dumps(
                {
                    "type": "watch_complete",
                    "watch_id": watch_id,
                    "token_address": watch.get("token_address", ""),
                    "total_updates": watch.get("total_updates", 0),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        )
        await r.close()
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)


async def list_active_watches() -> list[dict[str, Any]]:
    """List all active watches."""
    now = datetime.now(UTC).timestamp()
    active = []
    expired = []

    for wid, watch in _watches.items():
        if watch.get("expires_at", 0) > now:
            active.append(
                {
                    "id": wid,
                    "token_address": watch["token_address"],
                    "chain": watch["chain"],
                    "status": watch["status"],
                    "updates": watch.get("total_updates", 0),
                    "created_at": watch["created_at"],
                }
            )
        else:
            expired.append(wid)

    # Clean up expired
    for wid in expired:
        _watches.pop(wid, None)
        _updates.pop(wid, None)

    return active


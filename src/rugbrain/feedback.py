"""
RAG Feedback Loop - Scanner results feed back into RAG
======================================================

When the SENTINEL scanner confirms a token is a scam/honeypot/rug,
this module adjusts the RAG document weights so similar patterns
get higher priority in future searches.

Flow:
  Scanner reports scam → Feedback endpoint called
    → Find matching RAG documents (by address, pattern, chain)
    → Boost their weight in Redis metadata
    → FAISS index marked for rebuild on next cycle
    → Similar documents get +weight from confirmed patterns

Also tracks false positives:
  Scanner clears a token → Penalize matching RAG docs
  → Reduce weight → fewer false alarms

This creates a LEARNING LOOP: the more the scanner runs,
the smarter the RAG gets.
"""

import contextlib
import logging
from typing import Any

logger = logging.getLogger("rag.feedback")

# Weight adjustment constants
SCAM_CONFIRMED_BOOST = 0.3  # +30% weight for confirmed scam matches
FALSE_POSITIVE_PENALTY = -0.2  # -20% weight for false positives
SCANNER_HIT_BOOST = 0.05  # +5% per scanner hit on a document
MAX_WEIGHT = 3.0  # Cap at 3x original weight
MIN_WEIGHT = 0.1  # Floor at 10% original


async def record_scanner_result(
    address: str,
    chain: str,
    verdict: str,  # "scam", "honeypot", "rug", "safe", "unknown"
    confidence: float,  # 0.0 - 1.0
    flags: list[str] | None = None,
    token_name: str = "",
) -> dict[str, Any]:
    """
    Record a scanner verdict and adjust RAG weights accordingly.

    Called by SENTINEL scanner after each token scan.
    """
    import os as _os

    import redis.asyncio as aioredis

    r = aioredis.Redis(
        host=_os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(_os.getenv("REDIS_PORT", "6379")),
        password=_os.getenv("REDIS_PASSWORD", ""),
        db=int(_os.getenv("REDIS_DB", "0")),
        socket_connect_timeout=3,
        socket_timeout=3,
        decode_responses=True,
    )

    adjustments = {"boosted": 0, "penalized": 0, "errors": 0}

    try:
        is_scam = verdict in ("scam", "honeypot", "rug", "critical")
        adjustment = (
            SCAM_CONFIRMED_BOOST if is_scam else FALSE_POSITIVE_PENALTY if verdict == "safe" else 0
        )

        if adjustment == 0:
            await r.aclose()  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose
            return {"status": "no_action", "verdict": verdict}

        # Find RAG documents matching this address
        # Search by address in known_scams collection
        doc_ids = await r.smembers(f"rag:entity:address:{address.lower()}")

        if not doc_ids:
            # Try fuzzy - search for partial address in content
            # Use Redis scan for efficiency
            cursor = 0
            pattern = "rag:known_scams:*"
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    try:
                        doc = await r.get(key)
                        if doc and address.lower() in doc.lower():
                            doc_id = key.split(":")[-1]
                            doc_ids.add(doc_id)
                    except Exception:
                        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                if cursor == 0:
                    break

        # Also find documents with similar scam patterns (by flags)
        if is_scam and flags:
            cursor = 0
            pattern = "rag:known_scams:*"
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    try:
                        doc = await r.get(key)
                        if doc:
                            doc_lower = doc.lower()
                            matching_flags = sum(1 for f in flags if f.lower() in doc_lower)
                            if matching_flags >= 2:  # At least 2 flags match
                                doc_id = key.split(":")[-1]
                                doc_ids.add(doc_id)
                    except Exception:
                        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                if cursor == 0:
                    break

        # Apply weight adjustments
        for doc_id in doc_ids:
            try:
                # Read current weight
                weight_key = f"rag:weight:{doc_id}"
                current_weight = await r.get(weight_key)
                current = float(current_weight) if current_weight else 1.0

                # Adjust
                new_weight = current + (adjustment * confidence)
                new_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, new_weight))

                await r.set(weight_key, str(new_weight))

                if adjustment > 0:
                    adjustments["boosted"] += 1
                else:
                    adjustments["penalized"] += 1

            except Exception as e:
                adjustments["errors"] += 1
                logger.debug("Weight adjustment error for %s: %s", doc_id, e)

        # Record scanner hit count for analytics
        if doc_ids:
            hit_key = f"rag:scanner_hits:{address.lower()}"
            await r.incr(hit_key)
            await r.expire(hit_key, 86400 * 30)  # 30 day TTL

        # Mark FAISS for rebuild on next firehose cycle
        if adjustments["boosted"] + adjustments["penalized"] > 0:
            await r.set("rag:faiss:dirty", "1")

        logger.info(
            f"RAG feedback: {verdict} for {address} → "
            f"+{adjustments['boosted']} boosted, "
            f"-{adjustments['penalized']} penalized"
        )

        await r.aclose()  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose
        return {
            "status": "ok",
            "verdict": verdict,
            "adjustment": adjustment,
            "documents_adjusted": adjustments["boosted"] + adjustments["penalized"],
            "boosted": adjustments["boosted"],
            "penalized": adjustments["penalized"],
            "faiss_marked_dirty": adjustments["boosted"] + adjustments["penalized"] > 0,
        }

    except Exception as e:
        logger.error("RAG feedback error: %s", e)
        with contextlib.suppress(Exception):
            await r.aclose()  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose
        return {"status": "error", "detail": str(e)}


async def get_document_weight(doc_id: str) -> float:
    """Get the current learned weight for a RAG document."""
    import os as _os

    import redis.asyncio as aioredis

    try:
        r = aioredis.Redis(
            host=_os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(_os.getenv("REDIS_PORT", "6379")),
            password=_os.getenv("REDIS_PASSWORD", ""),
            db=int(_os.getenv("REDIS_DB", "0")),
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        weight = await r.get(f"rag:weight:{doc_id}")
        await r.aclose()  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose
        return float(weight) if weight else 1.0
    except Exception:
        return 1.0


async def get_feedback_stats() -> dict[str, Any]:
    """Get feedback loop statistics."""
    import os as _os

    import redis.asyncio as aioredis

    try:
        r = aioredis.Redis(
            host=_os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(_os.getenv("REDIS_PORT", "6379")),
            password=_os.getenv("REDIS_PASSWORD", ""),
            db=int(_os.getenv("REDIS_DB", "0")),
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )

        # Count weight-adjusted documents
        weight_keys = 0
        total_weight = 0.0
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="rag:weight:*", count=500)
            for key in keys:
                try:
                    w = await r.get(key)
                    if w:
                        weight_keys += 1
                        total_weight += float(w)
                except Exception:
                    logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
            if cursor == 0:
                break

        avg_weight = total_weight / weight_keys if weight_keys > 0 else 1.0
        faiss_dirty = await r.get("rag:faiss:dirty") == "1"

        await r.aclose()  # type: ignore[attr-defined]  # redis.asyncio stub lacks aclose
        return {
            "documents_with_weights": weight_keys,
            "average_weight": round(avg_weight, 3),
            "faiss_needs_rebuild": faiss_dirty,
            "weight_range": f"{MIN_WEIGHT} - {MAX_WEIGHT}",
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}

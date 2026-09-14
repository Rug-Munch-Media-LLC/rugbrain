"""Scanner → RAG feedback loop — live token scan results + confirmed scam marking.

Accepts scan results from RugMunchScan (rmi-backend), embeds token metadata
and risk signals into Qdrant, and exposes vector-similarity search for
discovering similar tokens. Provides a bidirectional feedback channel:

  Scanner (rmi-backend)
      → POST /api/v1/feedback/scan-result      (ingest new scan)
      → POST /api/v1/feedback/scam-confirmed    (mark confirmed scam)
      ← GET  /api/v1/similar/{address}           (vector similarity search)

All embeddings use the existing Qwen3-Embedding-4B pipeline via
``rugbrain.embeddings.get_embedding()`` so the feedback collection shares
the same 1024-dim vector space as the main threat_intel_v2 collection.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from rugbrain.ingest_common import doc_from_article, store_documents
from rugbrain.qdrant_store import QdrantVectorStore

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

FEEDBACK_COLLECTION = os.getenv("RAG_FEEDBACK_COLLECTION", "scanner_feedback")
FEEDBACK_MIN_SIMILARITY = float(os.getenv("RAG_FEEDBACK_MIN_SIMILARITY", "0.5"))

# ── Router ───────────────────────────────────────────────────────────────────

scanner_feedback_router = APIRouter(prefix="/api/v1", tags=["scanner-feedback"])


# ── Ingest: scan result ──────────────────────────────────────────────────────


@scanner_feedback_router.post("/feedback/scan-result")
async def ingest_scan_result(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    """Accept a scan result from RugMunchScan and store in Qdrant.

    Body (required fields)::

        {
          "token_address": "0x...",
          "chain": "ethereum",
          "token_name": "SuperMoon Token",        (optional — falls back to address)
          "risk_score": 0.85,
          "risk_level": "high|critical|medium|low",
          "scanner_version": "1.0.0",
          "signals": ["honeypot", "no_liquidity", ...],
          "deployer_address": "0x...",             (optional)
        }

    On receipt the metadata is embedded, stored in the
    ``scanner_feedback`` Qdrant collection, and also published to the
    ``rmi:rag:feedback`` Redis channel for real-time subscribers.
    """
    address = str(data.get("token_address", "")).strip().lower()
    if not address:
        raise HTTPException(status_code=400, detail={"error": "token_address is required"})

    chain = str(data.get("chain", "ethereum")).strip().lower()
    token_name = str(data.get("token_name", "")).strip() or address
    risk_score = float(data.get("risk_score", 0.0))
    risk_level = str(data.get("risk_level", "unknown")).strip().lower()
    signals = data.get("signals", []) or []
    deployer = str(data.get("deployer_address", "")).strip().lower()

    # Build a rich metadata payload for embedding + Qdrant storage.
    signals_text = ", ".join(str(s) for s in signals) if signals else "none"
    content = (
        f"Token: {token_name} ({address}) on {chain}. "
        f"Risk: {risk_level} (score={risk_score:.2f}). "
        f"Signals: {signals_text}. "
        f"Deployer: {deployer or 'unknown'}."
    )[:5000]

    try:
        from rugbrain.qwen_embedders import Qwen3Embedder

        embedder = Qwen3Embedder()  # provider-aware (deepinfra-first when keyed)
        store = QdrantVectorStore()
        doc = doc_from_article(
            title=token_name,
            content=content,
            url=f"chain://{chain}/token/{address}",
            source="scanner-feedback",
            collection=FEEDBACK_COLLECTION,
            category="scan_result",
            severity=risk_level,
            chain=chain,
            metadata={
                "token_address": address,
                "token_name": token_name,
                "chain": chain,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "signals": signals,
                "deployer_address": deployer,
                "scanner_version": str(data.get("scanner_version", "")),
                "ingested_at": datetime.now(UTC).isoformat(),
                "scam_confirmed": False,
            },
        )
        result = await store_documents([doc], embedder, store)
        await store.close()
    except Exception as exc:
        logger.warning("scanner_feedback_ingest_failed address=%s err=%s", address, exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "ingest_failed", "message": str(exc)[:200]},
        ) from exc

    # Publish to Redis feedback channel for real-time subscribers.
    try:
        from rugbrain.search_service import _get_redis

        rd = await _get_redis()
        await rd.publish(
            "rmi:rag:feedback",
            __import__("json").dumps(
                {
                    "event": "scan_result",
                    "token_address": address,
                    "chain": chain,
                    "risk_score": risk_score,
                    "risk_level": risk_level,
                    "ingested_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
    except Exception:
        logger.debug("scanner_feedback_redis_publish_failed (non-critical)")

    return {
        "status": "ingested",
        "token_address": address,
        "chain": chain,
        "collection": FEEDBACK_COLLECTION,
        "inserted": result.get("inserted", 0),
        "chunks": result.get("chunks", 0),
    }


# ── Mark confirmed scam ──────────────────────────────────────────────────────


@scanner_feedback_router.post("/feedback/scam-confirmed")
async def mark_scam_confirmed(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    """Mark a token as a confirmed scam in the feedback collection.

    Body::

        {
          "token_address": "0x...",
          "chain": "ethereum",         (optional — defaults to ethereum)
          "confirmed_by": "reporter",  (optional)
          "evidence_url": "https://...", (optional)
          "notes": "...",               (optional)
        }

    Updates the Qdrant point payload to set ``scam_confirmed: true``
    and appends the confirmation metadata. Points are matched by
    ``token_address`` and ``chain`` payload filters.
    """
    address = str(data.get("token_address", "")).strip().lower()
    if not address:
        raise HTTPException(status_code=400, detail={"error": "token_address is required"})

    chain = str(data.get("chain", "ethereum")).strip().lower()
    confirmed_by = str(data.get("confirmed_by", "scanner")).strip()
    evidence_url = str(data.get("evidence_url", "")).strip()
    notes = str(data.get("notes", "")).strip()

    store = QdrantVectorStore()
    try:
        # Search for existing point by token_address + chain payload filter.
        from rugbrain.embeddings import get_embedding

        embedding = await get_embedding(address)
        results = await store.hybrid_search(
            query_text=address,
            query_embedding=embedding,
            collection=FEEDBACK_COLLECTION,
            limit=5,
            min_similarity=0.92,
        )

        update_count = 0
        for r in results:
            r_address = str((r.get("metadata") or {}).get("token_address", "")).lower()
            r_chain = str((r.get("metadata") or {}).get("chain", "")).lower()
            if r_address == address and r_chain == chain:
                # Re-insert with scam_confirmed flag — update via upsert.
                try:
                    meta = dict(r.get("metadata") or {})
                    meta["scam_confirmed"] = True
                    meta["confirmed_by"] = confirmed_by
                    meta["confirmed_at"] = datetime.now(UTC).isoformat()
                    if evidence_url:
                        meta["evidence_url"] = evidence_url
                    if notes:
                        meta["confirmed_notes"] = notes
                    severity = "high"

                    await store.insert(
                        doc_id=r.get("id", ""),
                        collection=FEEDBACK_COLLECTION,
                        embedding=r.get("embedding", embedding),
                        content=r.get("content", ""),
                        metadata=meta,
                        source="scanner-feedback",
                        severity=severity,
                        chain=chain,
                    )
                    update_count += 1
                except Exception as e:
                    logger.warning("scam_confirmed_update_failed address=%s err=%s", address, e)
    except Exception as exc:
        logger.warning("scam_confirmed_search_failed address=%s err=%s", address, exc)
        update_count = 0
    finally:
        await store.close()

    return {
        "status": "confirmed" if update_count else "not_found",
        "token_address": address,
        "chain": chain,
        "points_updated": update_count,
        "confirmed_at": datetime.now(UTC).isoformat(),
    }


# ── Similar token search ─────────────────────────────────────────────────────


@scanner_feedback_router.get("/similar/{address}")
async def find_similar_tokens(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum", description="Chain to scope the search"),
    collection: str = Query(
        default=FEEDBACK_COLLECTION,
        description="Qdrant collection to search (default: scanner_feedback)",
    ),
    limit: int = Query(default=10, ge=1, le=50, description="Max results"),
    min_similarity: float = Query(
        default=FEEDBACK_MIN_SIMILARITY, ge=0.1, le=1.0, description="Minimum similarity threshold"
    ),
) -> dict[str, Any]:
    """Find similar tokens via vector similarity search.

    Embeds the token address + chain into the 1024-dim vector space and
    returns the nearest neighbors from the feedback collection, scoring
    by cosine similarity. Useful for discovering tokens with similar
    risk profiles, deployer patterns, or scam signals::

        curl "http://localhost:8012/api/v1/similar/0xabc...?chain=ethereum&limit=5"
    """
    address = address.strip().lower()
    if not address:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "address is required",
                "message": "provide a token or contract address",
            },
        )

    try:
        from rugbrain.embeddings import get_embedding

        embedding = await get_embedding(f"token {address} on {chain}")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "embedding_unavailable", "message": str(exc)[:200]},
        ) from exc

    store = QdrantVectorStore()
    try:
        results = await store.hybrid_search(
            query_text=f"token {address} on {chain}",
            query_embedding=embedding,
            collection=collection,
            limit=limit,
            min_similarity=min_similarity,
        )

        similar: list[dict[str, Any]] = []
        for r in results:
            meta = r.get("metadata") or {}
            # Exclude self-match (exact same address + chain).
            if (
                str(meta.get("token_address", "")).lower() == address
                and str(meta.get("chain", "")).lower() == chain.lower()
            ):
                continue
            similar.append(
                {
                    "token_address": meta.get("token_address", ""),
                    "token_name": meta.get("token_name", ""),
                    "chain": meta.get("chain", ""),
                    "risk_score": meta.get("risk_score", 0.0),
                    "risk_level": meta.get("risk_level", "unknown"),
                    "scam_confirmed": meta.get("scam_confirmed", False),
                    "signals": meta.get("signals", []),
                    "similarity": round(r.get("similarity", 0.0), 4),
                    "deployer_address": meta.get("deployer_address", ""),
                }
            )

        return {
            "query_address": address,
            "query_chain": chain,
            "similar": similar,
            "total": len(similar),
            "collection": collection,
            "searched_at": datetime.now(UTC).isoformat(),
        }
    finally:
        await store.close()


# ── Statistics endpoint ──────────────────────────────────────────────────────


@scanner_feedback_router.get("/feedback/stats")
async def feedback_stats(request: Request) -> dict[str, Any]:
    """Return feedback-collection statistics: total points and confirmed scams.

    Scrolls the ``scanner_feedback`` collection and counts results. Best
    effort — returns empty stats when Qdrant is unreachable.
    """
    store = QdrantVectorStore()
    try:
        total = 0
        confirmed = 0
        offset: str | None = None
        for _ in range(20):  # up to 2000 points scanned
            results, offset = await store.scroll(FEEDBACK_COLLECTION, limit=100, offset=offset)
            if not results:
                break
            total += len(results)
            for r in results:
                if (r.get("metadata") or {}).get("scam_confirmed"):
                    confirmed += 1
            if not offset:
                break
    except Exception as exc:
        logger.warning("feedback_stats_failed: %s", exc)
        return {
            "collection": FEEDBACK_COLLECTION,
            "total_points": 0,
            "confirmed_scams": 0,
            "error": str(exc)[:200],
        }
    finally:
        await store.close()

    return {
        "collection": FEEDBACK_COLLECTION,
        "total_points": total,
        "confirmed_scams": confirmed,
        "scanned_at": datetime.now(UTC).isoformat(),
    }


__all__ = [
    "FEEDBACK_COLLECTION",
    "find_similar_tokens",
    "ingest_scan_result",
    "mark_scam_confirmed",
    "scanner_feedback_router",
]

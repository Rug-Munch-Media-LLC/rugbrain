"""Ingestion route: ``POST /api/v1/ingest``.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix here — the parent router in ``endpoints.py`` supplies
``/api/v1``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from rugbrain.endpoints import RATE_LIMITS, _check_rate_limit

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["rag-optimization"])


class IngestDocument(BaseModel):
    doc_id: str = ""
    content: str
    metadata: dict[str, Any] = {}
    collection: str = "default"
    source: str = "api"
    severity: str = "medium"
    chain: str = ""
    audience: Literal["public"] = "public"


class IngestRequest(BaseModel):
    documents: list[IngestDocument]


@router.post("/ingest")
async def ingest_documents(
    request: Request,
    body: IngestRequest,
) -> dict[str, Any]:
    """Accept documents, embed them, and store in Qdrant.

    Each document is embedded using the Qwen3 embedder and inserted into the
    specified collection. Returns per-document status (ok/failed).

    Example::

        curl -X POST "http://localhost:8012/api/v1/ingest" \\
          -H "Content-Type: application/json" \\
          -H "X-API-Key: ..." \\
          -d '{"documents":[{"content":"Flash loan attack on...","collection":"defi_exploits","severity":"high"}]}'
    """
    from rugbrain.embeddings import get_embedding
    from rugbrain.knowledge_boundary import (
        CollectionNotPublic,
        require_public_collection,
        sanitize_public_result,
    )
    from rugbrain.qdrant_store import QdrantVectorStore

    if not body.documents:
        return {"status": "ok", "ingested": 0, "results": []}

    try:
        normalized_collections = [
            require_public_collection(doc.collection) for doc in body.documents
        ]
    except CollectionNotPublic as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "collection_not_available"},
        ) from exc

    # Rate limit by client IP
    hour_key = datetime.now(UTC).strftime("%Y%m%d%H")
    client_ip = request.client.host if request.client else "unknown"
    allowed, ttl = await _check_rate_limit(client_ip, "search", hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": "search",
                "limit": RATE_LIMITS["search"],
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    store = QdrantVectorStore()
    results: list[dict[str, Any]] = []

    for doc, collection in zip(body.documents, normalized_collections, strict=True):
        try:
            embedding = await get_embedding(doc.content)
            if not embedding:
                results.append(
                    {"doc_id": doc.doc_id, "status": "failed", "error": "embedding_failed"}
                )
                continue

            doc_id = doc.doc_id or doc.content[:64]
            ok = await store.insert(
                doc_id=doc_id,
                collection=collection,
                embedding=embedding,
                content=doc.content,
                metadata=sanitize_public_result(doc.metadata),
                source=sanitize_public_result(doc.source),
                severity=doc.severity,
                chain=doc.chain,
            )
            results.append(
                {"doc_id": doc_id, "status": "ok" if ok else "failed", "collection": collection}
            )
        except Exception as e:
            logger.warning("ingest_failed doc_id=%s error=%s", doc.doc_id, e)
            results.append({"doc_id": doc.doc_id, "status": "failed", "error": str(e)[:200]})

    return {
        "status": "ok",
        "ingested": sum(1 for r in results if r["status"] == "ok"),
        "total": len(results),
        "results": results,
    }

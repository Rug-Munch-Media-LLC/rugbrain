"""``scam_check_router`` — the address scam check under ``/api/v1/rag/v2``.

The verdict is address-scoped label evidence only (``scam_verdict``). The
semantic-search leg was removed deliberately: an address is an identifier, and
a nearest-neighbour query over a corpus in which *every* row is a scam/threat
record returns high-severity rows for any input, including a $30B stablecoin.
Similarity to the word "scam" is not evidence about the address, and it cost
8.7 s per call. Callers wanting topical context use ``/api/v1/search``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from rugbrain.knowledge_boundary import sanitize_public_result
from rugbrain.routes.search_pkg._shared import enforce_rate_limit
from rugbrain.scam_verdict import decide_verdict

logger = logging.getLogger(__name__)

scam_check_router: APIRouter = APIRouter(prefix="/api/v1/rag/v2", tags=["scam-check"])


@scam_check_router.get("/scam-check")
async def scam_check(
    request: Request,
    address: str,
    chain: str = "ethereum",
    limit: int = Query(default=10, ge=1, le=100),
    tier: str = Query(default="free", pattern="^(free|pro)$"),
) -> dict[str, Any]:
    """Return the scam verdict for one address, plus every row it rests on."""
    await enforce_rate_limit(request, tier)

    from rugbrain.search_service import exact_address_hits

    hits = await exact_address_hits(address)
    matches = [
        sanitize_public_result(
            {
                "id": str(payload.get("doc_id", "") or ""),
                "content": (payload.get("content", "") or "")[:300],
                "similarity": 1.0,
                "severity": str(payload.get("severity", "") or ""),
                "collection": collection,
                "source": str(payload.get("source", "") or ""),
            }
        )
        for collection, payload in hits[:limit]
    ]

    verdict = decide_verdict(address, hits)
    return {
        "address": address,
        "chain": chain,
        "scam_risk": verdict["scam_risk"],
        "confidence": verdict["confidence"],
        "verdict_reason": verdict["verdict_reason"],
        "evidence_scope": "address_scoped_only",
        "matches": matches,
        "total_matches": len(matches),
    }

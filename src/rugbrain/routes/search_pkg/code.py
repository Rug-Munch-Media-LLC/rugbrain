"""``GET /api/v1/search/crypto-code`` — public crypto-code search.

Serves only the redacted ``public_crypto_patterns`` projection; it never reads
ContextCrypto's internal collections and refuses requests to weaponize or
execute attacks.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["rag-optimization"])

@router.get("/search/crypto-code")

# ── Crypto Code Mode (ContextCrypto collections) ──────────────────────


@router.get("/search/crypto-code")
async def search_crypto_code_endpoint(
    q: str = Query(..., min_length=2, description="public crypto-code question"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Search reviewed public/open-source crypto code and exploit knowledge.

    This endpoint never reads ContextCrypto's internal collections. It serves
    only the redacted ``public_crypto_patterns`` projection and refuses
    requests to weaponize or execute attacks.
    """
    from rugbrain.public_crypto_code import public_code_request_allowed

    if not public_code_request_allowed(q):
        return {
            "query": q,
            "routed": False,
            "blocked": True,
            "reason": "unsafe_request",
            "results": [],
            "total": 0,
            "message": "I can help with defensive analysis, public advisories, and authorized code review.",
        }

    from rugbrain.public_crypto_code import search_public_crypto_code

    return await search_public_crypto_code(q, top_k=limit)

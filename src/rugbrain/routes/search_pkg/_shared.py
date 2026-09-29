"""Shared imports and the rate-limit guard for the search routes.

One job: hold what every search endpoint needs so the endpoint modules stay
readable, and expose the per-client rate limit as a single function.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel

from rugbrain import endpoints as _ep
from rugbrain.knowledge_boundary import (
    CollectionNotPublic,
    require_public_collection,
)

logger = logging.getLogger(__name__)

RRF_K = 60


class MultiSearchRequest(BaseModel):
    """Body for ``POST /api/v1/search/multi``."""

    query: str = ""
    collections: list[str] = ["threat_intel_v2"]
    limit: int = 10
    limit_per_collection: int = 20
    rerank: bool = False


def client_ip(request: Request) -> str:
    """Peer address only — headers like ``X-Forwarded-For`` are spoofable."""
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(request: Request, tier: str) -> None:
    """Raise 429 when the client exceeds ``tier``'s hourly budget.

    The identifier is the peer IP only. Client-controlled headers
    (``X-Device-Id`` and friends) are spoofable, so trusting one would let a
    caller rotate headers to bypass the limit.
    """
    hour_key = datetime.now(UTC).strftime("%Y%m%d%H")
    allowed, ttl = await _ep._check_rate_limit(client_ip(request), tier, hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": tier,
                "limit": _ep.RATE_LIMITS.get(tier, _ep.RATE_LIMITS["free"]),
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )


def resolve_public_collections(collections: list[str]) -> list[str]:
    """Reject any collection outside the public allowlist (403)."""
    try:
        return [require_public_collection(c) for c in collections]
    except CollectionNotPublic as exc:
        raise HTTPException(
            status_code=403, detail={"error": "collection_not_available"}
        ) from exc

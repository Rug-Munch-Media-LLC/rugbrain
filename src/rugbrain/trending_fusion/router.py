"""HTTP surface of the one trending engine (frozen envelope, HTTP 200)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from rugbrain.trending_fusion.api import DEFAULT_LIMIT, rugbrain_trending_fused

router = APIRouter(prefix="/api/v1", tags=["trending"])


@router.get("/trending")
async def get_trending(chain: str = "solana", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Ranked tokens for one chain, with evidence and typed refusals."""
    return await rugbrain_trending_fused(chain=chain, limit=limit)

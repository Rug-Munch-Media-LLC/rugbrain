"""Content-distribution routes — drafts for the bot publisher.

GET /api/v1/content/drafts?channel=main|alerts|news|scans|all
POST /api/v1/content/ack
Auth via the global X-API-Key middleware.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("rugbrain.content_routes")

router = APIRouter(prefix="/api/v1/content", tags=["content"])

_VALID_CHANNELS = {"main", "alerts", "news", "scans"}


class AckBody(BaseModel):
    cycle_id: str = Field(..., min_length=1, max_length=64)
    posted: list[dict[str, str]] = Field(default_factory=list)


@router.get("/drafts")
async def get_drafts(
    channel: str = Query(default="all", description="main|alerts|news|scans|all"),
) -> dict[str, Any]:
    if channel != "all" and channel not in _VALID_CHANNELS:
        raise HTTPException(
            status_code=422, detail=f"channel must be one of {_VALID_CHANNELS} or 'all'"
        )

    from rugbrain.content_engine import generate_channel_drafts

    cycle_id = f"c{int(time.time())}"
    all_drafts = await generate_channel_drafts(cycle_id=cycle_id)

    payload: dict[str, Any] = (
        {ch: ds for ch, ds in all_drafts.items()}
        if channel == "all"
        else {channel: all_drafts.get(channel, [])}
    )
    return {
        "cycle_id": cycle_id,
        "channels": payload,
        "total": sum(len(ds) for ds in payload.values()),
    }


@router.post("/ack")
async def ack_posted(body: AckBody) -> dict[str, Any]:
    logger.info("content_ack cycle=%s posted=%d", body.cycle_id, len(body.posted))
    return {"status": "ok", "cycle_id": body.cycle_id, "acked": len(body.posted)}

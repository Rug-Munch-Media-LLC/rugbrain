"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Daily Rug publication surface.

POST /api/v1/content/daily-rug         generate + publish (idempotent upsert)
GET  /api/v1/content/daily-rug/{slug}  read one published story

Auth via the global X-API-Key middleware.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rugbrain.daily_rug_publish import publish_daily_rug
from rugbrain.daily_rug_store import default_store

router = APIRouter(prefix="/api/v1/content/daily-rug", tags=["content"])


class PublishBody(BaseModel):
    topic: str = Field(..., min_length=3, max_length=200)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("")
async def publish(body: PublishBody) -> dict[str, Any]:
    return await publish_daily_rug(body.topic, date=body.date)


@router.get("/{slug}")
async def get_story(slug: str) -> dict[str, Any]:
    story = default_store().get(slug)
    if story is None:
        raise HTTPException(status_code=404, detail=f"no published story for {slug}")
    return story

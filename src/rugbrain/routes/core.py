"""Core service routes: health probe + embedding passthrough.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix here — the parent router in ``endpoints.py`` supplies
``/api/v1``.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router: APIRouter = APIRouter(tags=["rag-optimization"])


class EmbedRequest(BaseModel):
    text: str
    # Target dim (MRL truncate-down, L2-renormalized). Consumers embedding
    # into a specific collection pass its dim (e.g. news_v2=2560); omit to
    # get the model's native output.
    dimensions: int | None = None


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check — verifies service is running."""
    return {"status": "ok", "service": "rugbrain"}


@router.post("/embed")
async def embed_text(request: EmbedRequest) -> dict[str, Any]:
    """Embed text via Qwen3-Embedding-4B and return vector."""
    import httpx

    int_url = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{int_url}/embeddings",
                json={"input": request.text, "model": "qwen3-embedding-4b"},
            )
            if r.status_code == 200:
                data = r.json()
                vec = data.get("data", [{}])[0].get("embedding", [])
                if request.dimensions and len(vec) > request.dimensions:
                    from rugbrain.dim_policy import normalize_dims

                    vec = normalize_dims(vec, request.dimensions)
                return {"embedding": vec, "dim": len(vec)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedder unavailable: {e}") from e
    return {"embedding": [], "dim": 0}


__all__ = ["EmbedRequest", "embed_text", "health", "router"]

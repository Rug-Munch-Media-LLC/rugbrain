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
    """Embed text via Qwen3-Embedding-4B and return vector.

    Same-space fallback: local :8004 saturated (migration) -> OpenRouter
    qwen/qwen3-embedding-4b (verified identical 2560-dim space).
    """
    import httpx

    int_url = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1")
    model = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
    attempts: list[tuple[str, str]] = [
        ("local", f"{int_url.rstrip('/')}/embeddings"),
    ]
    or_key = os.getenv("OPENROUTER_API_KEY", "")
    if or_key:
        attempts.append(
            (
                "openrouter",
                os.getenv("OR_QWEN_EMBED_URL", "https://openrouter.ai/api/v1/embeddings"),
            )
        )
    last_exc: Exception | None = None
    for name, url in attempts:
        headers = {"Authorization": f"Bearer {or_key}"} if name == "openrouter" else {}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    url,
                    json={"input": request.text, "model": model},
                    headers=headers,
                )
                if r.status_code == 200:
                    data = r.json()
                    vec = data.get("data", [{}])[0].get("embedding", [])
                    if request.dimensions and len(vec) > request.dimensions:
                        from rugbrain.dim_policy import normalize_dims

                        vec = normalize_dims(vec, request.dimensions)
                    return {"embedding": vec, "dim": len(vec)}
                last_exc = RuntimeError(f"{name} HTTP {r.status_code}")
        except Exception as e:
            last_exc = e
    raise HTTPException(status_code=502, detail=f"Embedder unavailable: {last_exc}")


__all__ = ["EmbedRequest", "embed_text", "health", "router"]

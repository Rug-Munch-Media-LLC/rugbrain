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


def _embeddings_endpoint(base_url: str) -> str:
    """Normalize provider URLs that may be configured as a base or endpoint."""
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/embeddings") else f"{normalized}/embeddings"


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check — verifies service is running."""
    return {"status": "ok", "service": "rugbrain"}


@router.post("/embed")
async def embed_text(request: EmbedRequest) -> dict[str, Any]:
    """Return an embedding without exposing implementation details.

    Public responses intentionally omit provider, model, and routing metadata.
    """
    import httpx

    from rugbrain.multi_embedder import _load_api_key

    int_url = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")
    model = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")
    attempts: list[tuple[str, str, str, str]] = []  # (name, url, model, bearer)
    di_key = _load_api_key("DEEPINFRA_API_KEY")
    if di_key:
        attempts.append(
            (
                "deepinfra",
                _embeddings_endpoint(
                    os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1")
                ),
                os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B"),
                di_key,
            )
        )
    # 2026-10-17: the local llama.cpp lane (:8004) is not running — its socat
    # forward targets 127.0.0.1:8004, which has no listener, so the attempt
    # burns the full 30s timeout and then the route raises 502. Probe it first
    # and only append when it actually answers. DeepInfra (primary) and the
    # multi_embedder siliconflow lane still cover the space.
    sf_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if sf_key:
        attempts.append(
            (
                "siliconflow",
                _embeddings_endpoint(
                    os.getenv(
                        "SILICONFLOW_EMBED_URL",
                        "https://api.siliconflow.com/v1/embeddings",
                    )
                ),
                os.getenv("SILICONFLOW_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B"),
                sf_key,
            )
        )
    import socket as _socket

    def _local_lane_alive() -> bool:
        host, _, port = int_url.partition("://")[2].partition("/")[0].partition(":")
        if not port.isdigit():
            return False
        try:
            with _socket.create_connection((host or "127.0.0.1", int(port)), timeout=1.5):
                return True
        except OSError:
            return False

    if _local_lane_alive():
        attempts.append(("local", _embeddings_endpoint(int_url), model, ""))
    last_exc: Exception | None = None
    for name, url, req_model, bearer in attempts:
        # Local probe must fail FAST so the remote fallback engages inside
        # the caller's budget (a wedged :8004 otherwise eats 30s).
        timeout = 20.0 if name == "deepinfra" else 30.0
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    url,
                    json={"input": request.text, "model": req_model},
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

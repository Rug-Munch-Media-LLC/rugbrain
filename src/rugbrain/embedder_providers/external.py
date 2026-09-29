"""External embedding providers — OpenRouter (Nemotron) and HuggingFace.

These are the rungs BELOW the canonical Qwen3-Embedding-8B lane: cloud
fallbacks with a different vector dim, used only when the canonical lane is
unavailable. The 2026-08-25 default for the OpenRouter primary was a
placeholder slug ("openrouter/your-model-here"), which made the cloud rung
fire at a nonexistent model whenever ``RAG_PRIMARY_EMBED_MODEL`` was unset;
the verified-live free slug is the default now.

The OpenRouter rate-limit FALLBACK defaults to the canonical model rather than
the retired 4B one: falling back across vector spaces silently produces
vectors that store and search but are not comparable with the corpus.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")

# 2026-08-25: honor both the canonical env name and the one .env.rag ships.
OR_PRIMARY_EMBED = os.getenv(
    "RAG_PRIMARY_EMBED_MODEL",
    os.getenv("RAG_OPENROUTER_EMBED_MODEL", "nvidia/nemotron-3-embed-1b:free"),
)
OR_FALLBACK_EMBED = os.getenv("RAG_LOCAL_EMBED_MODEL", "qwen3-embedding-8b")


class OpenRouterEmbedder:
    """OpenRouter embedding API - NVIDIA Nemotron primary, canonical fallback."""

    BASE = "https://openrouter.ai/api/v1/embeddings"
    MODEL = OR_PRIMARY_EMBED
    FALLBACK = OR_FALLBACK_EMBED

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or OPENROUTER_KEY
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        async def _try_embed(m: str) -> list[list[float]]:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {"model": m, "input": texts}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(self.BASE, headers=headers, json=payload)
                if resp.status_code == 429:
                    raise RuntimeError("RATE_LIMITED")
                resp.raise_for_status()
                data = resp.json()
                return [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]

        try:
            return await _try_embed(model or self.MODEL)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and model != self.FALLBACK:
                logger.warning("NVIDIA embed rate-limited, falling back to %s", self.FALLBACK)
                return await _try_embed(self.FALLBACK)
            raise

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class HuggingFaceEmbedder:
    """HuggingFace Inference API - free tier fallback."""

    BASE = "https://api-inference.huggingface.co/models"
    MODEL = "qwen3-embedding-8b"
    DIMS = int(os.getenv("HF_EMBED_DIMS", "1024"))

    def __init__(self, token: str | None = None):
        self.token = token or HF_TOKEN
        if not self.token:
            raise ValueError("HUGGINGFACE_TOKEN required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true",
        }
        url = f"{self.BASE}/{model or self.MODEL}"
        results: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for text in texts:
                resp = await client.post(url, headers=headers, json={"inputs": text})
                if resp.status_code == 503:  # model loading
                    await asyncio.sleep(3)
                    resp = await client.post(url, headers=headers, json={"inputs": text})
                resp.raise_for_status()
                results.append(_coerce_hf_shape(resp.json(), self.dims))
        return results

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


def _coerce_hf_shape(data: object, dims: int) -> list[float]:
    """HF feature-extraction returns ``[[float]]``; accept both nesting shapes."""
    if isinstance(data, list) and data and isinstance(data[0], list):
        return data[0]
    if isinstance(data, list) and data:
        return data
    logger.warning("HF returned unexpected shape: %s", type(data))
    return [0.0] * dims

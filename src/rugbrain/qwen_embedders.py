#!/usr/bin/env python3
"""
Embedding provider classes — Qwen3-Embedding-4B via llama.cpp (primary),
OpenRouter/HuggingFace (external fallbacks), and specialized contract embedder.
"""

import asyncio
import logging
import os
from typing import Any

import numpy as np

from rugbrain.feature_extractors import extract_contract_features

logger = logging.getLogger(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")
OR_PRIMARY_EMBED = os.getenv("RAG_PRIMARY_EMBED_MODEL", "openrouter/your-model-here")
OR_FALLBACK_EMBED = os.getenv("RAG_LOCAL_EMBED_MODEL", "local/your-local-model")


class Qwen3Embedder:
    """llama.cpp Qwen3-Embedding-4B provider (OpenAI-compatible /embeddings).

    Serves 1024-dim vectors for all RAG ingestion/search paths.
    """

    EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1")
    MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
    DIMS = int(os.getenv("QWEN_EMBED_DIMS", "1024"))

    def __init__(self, embed_url: str | None = None):
        self.url = embed_url or os.getenv(
            "QWEN_EMBED_URL", os.getenv("QWEN_EMBED_URL", self.EMBED_URL)
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        results = []
        async with httpx.AsyncClient(timeout=120) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{self.url}/embeddings",
                        json={"model": self.MODEL, "input": [text[:8000]]},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        if data:
                            vec = data[0].get("embedding", [])
                            if len(vec) == self.DIMS:
                                results.append(vec)
                                continue
                            if len(vec) > self.DIMS:
                                # MRL truncate + renormalize (dim_policy)
                                from rugbrain.dim_policy import normalize_dims

                                results.append(normalize_dims(vec, self.DIMS))
                                continue
                            raise RuntimeError(
                                f"qwen_embedder dim mismatch: got {len(vec)}, want >= {self.DIMS}"
                            )
                        raise RuntimeError("qwen_embedder returned no data")
                except RuntimeError:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"qwen_embedder failed: {exc}") from exc
        return results

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class Qwen3FastEmbedder(Qwen3Embedder):
    """Fast utility tier — Qwen3-Embedding-0.6B on :8007.

    For near-duplicate detection, clustering, cache keys and internal
    similarity tools where BOTH sides of a comparison run on 0.6B.
    NEVER for corpus search: a 0.6B query against a 4B-embedded
    collection is a cross-model mismatch (same dims, different space).

    Inherits all dim-handling (MRL truncate + renormalize) from
    :class:`Qwen3Embedder`.
    """

    MODEL = "qwen3-embedding-0.6b"
    EMBED_URL = os.getenv("QWEN_FAST_URL", "http://172.22.0.1:8007/v1")
    DIMS = int(os.getenv("QWEN_FAST_DIMS", "1024"))

    def __init__(self, embed_url: str | None = None):
        # Bypass parent __init__ (it pins the deep URL/model); rebind ours.
        super().__init__(embed_url=embed_url or self.EMBED_URL)
        self.model = self.MODEL


class OpenRouterEmbedder:
    """OpenRouter embedding API - NVIDIA Nemotron primary, qwen3-embedding-4b fallback."""

    BASE = "https://openrouter.ai/api/v1/embeddings"
    MODEL = OR_PRIMARY_EMBED
    FALLBACK = OR_FALLBACK_EMBED

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or OPENROUTER_KEY
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        model_id = model or self.MODEL

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
            return await _try_embed(model_id)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and model_id != self.FALLBACK:
                logger.warning("NVIDIA embed rate-limited, falling back to %s", self.FALLBACK)
                return await _try_embed(self.FALLBACK)
            raise

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class HuggingFaceEmbedder:
    """HuggingFace Inference API - free tier fallback."""

    BASE = "https://api-inference.huggingface.co/models"
    MODEL = "qwen3-embedding-4b"
    DIMS = int(os.getenv("HF_EMBED_DIMS", "1024"))

    def __init__(self, token: str | None = None):
        self.token = token or HF_TOKEN
        if not self.token:
            raise ValueError("HUGGINGFACE_TOKEN required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        model_id = model or self.MODEL
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true",
        }
        url = f"{self.BASE}/{model_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            # HF API expects either a single string or list
            results: list[list[float]] = []
            for text in texts:
                resp = await client.post(url, headers=headers, json={"inputs": text})
                if resp.status_code == 503:  # model loading
                    await asyncio.sleep(3)
                    resp = await client.post(url, headers=headers, json={"inputs": text})
                resp.raise_for_status()
                data = resp.json()
                # HF feature-extraction returns nested lists: [[0.1, 0.2, ...]]
                # Handle both shapes: list of floats or list of list of floats
                if isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], list):
                        # Already [[float]] - take the first (mean-pooled usually)
                        results.append(data[0] if len(data) == 1 else data[0])  # noqa: RUF034
                    else:
                        # Flat [float] - wrap it
                        results.append(data)
                else:
                    logger.warning("HF returned unexpected shape: %s", type(data))
                    results.append([0.0] * self.DIMS)
            return results

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class ContractCodeEmbedder:
    """
    Specialized contract code embedder.
    Uses structural features + semantic embedding of decompiled code.
    Detects rug patterns even in obfuscated contracts.
    """

    @staticmethod
    async def embed(
        code: str, semantic_embedder: Any = None
    ) -> np.ndarray[Any, np.dtype[np.float32]]:
        """
        Generate a combined 320-dim contract embedding:
        - 128 dims: structural features (local)
        - 192 dims: semantic understanding (API) - only for non-trivial code
        """
        structural = extract_contract_features(code)

        if semantic_embedder and len(code) > 50:
            # Extract the meaningful parts (not just imports/boilerplate)
            # Take first 4000 chars - enough for the key logic
            code_snippet = code[:4000]
            try:
                semantic_vec = await semantic_embedder.embed_one(
                    f"Smart contract code: {code_snippet[:3000]}"
                )
                # Truncate or pad to 192
                semantic = np.array(semantic_vec[:192], dtype=np.float32)
                if len(semantic) < 192:
                    semantic = np.pad(semantic, (0, 192 - len(semantic)))
            except Exception as e:
                logger.warning("Semantic contract embedding failed: %s", e)
                semantic = np.zeros(192, dtype=np.float32)
        else:
            semantic = np.zeros(192, dtype=np.float32)

        return np.concatenate([structural, semantic])

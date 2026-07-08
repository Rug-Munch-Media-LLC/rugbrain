#!/usr/bin/env python3
"""
BGE / embedding provider classes — local, Ollama, OpenRouter, HuggingFace, and
specialized contract-code embedder.
"""

import asyncio
import logging
import os

import numpy as np

from rmi_rag.feature_extractors import extract_contract_features

logger = logging.getLogger(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")
OR_PRIMARY_EMBED = "rmi/bge-m3"
OR_FALLBACK_EMBED = "local/bge-small-en-v1.5"


class LocalBGEEmbedder:
    """
    Local BAAI BGE-small-en-v1.5 embedder.
    Runs on CPU, ~80MB RAM, 384-dim vectors.
    Loaded lazily - first call initializes the model.
    FALLBACK only. Primary is Ollama bge-m3.
    """

    MODEL_NAME = "BAAI/bge-small-en-v1.5"
    _model = None

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer

            cls._model = SentenceTransformer(cls.MODEL_NAME)
            logger.info("LocalBGEEmbedder loaded: %s (384d)", cls.MODEL_NAME)
        return cls._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        embeddings = await asyncio.to_thread(lambda: model.encode(texts, normalize_embeddings=True).tolist())
        return embeddings

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class OllamaBGEEmbedder:
    """Our own bge-m3 via Ollama. 1024d, always available, zero cost."""

    OLLAMA_URL = "http://172.19.0.1:11434"
    MODEL = "bge-m3"
    DIMS = 1024

    def __init__(self, ollama_url: str | None = None):
        self.url = ollama_url or os.getenv("OLLAMA_URL", self.OLLAMA_URL)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        results = []
        async with httpx.AsyncClient(timeout=3) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{self.url}/api/embeddings",
                        json={"model": self.MODEL, "prompt": text[:500]},
                    )
                    if resp.status_code == 200:
                        vec = resp.json().get("embedding", [])
                        if len(vec) == self.DIMS:
                            results.append(vec)
                            continue
                except Exception:
                    logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                # Fallback: zero vector
                results.append([0.0] * self.DIMS)
        return results

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class OpenRouterEmbedder:
    """OpenRouter embedding API - NVIDIA Nemotron primary, bge-m3 fallback."""

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
            async with httpx.AsyncClient(timeout=3) as client:
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
    MODEL = "BAAI/bge-m3"

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
                    results.append([0.0] * 1024)
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
    async def embed(code: str, semantic_embedder=None) -> np.ndarray:
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
                semantic_vec = await semantic_embedder.embed_one(f"Smart contract code: {code_snippet[:3000]}")
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

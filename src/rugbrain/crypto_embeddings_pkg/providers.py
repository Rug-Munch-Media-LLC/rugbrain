"""Provider-backed semantic embedding pipeline (tiered fallback)."""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.qwen_embedders import (
    ContractCodeEmbedder,
    HuggingFaceEmbedder,
    OpenRouterEmbedder,
    Qwen3Embedder,
)

logger = logging.getLogger(__name__)


class ProviderMixin:
    """Provider initialization + tiered semantic embedding."""

    _qwen: Qwen3Embedder | None
    _openrouter: OpenRouterEmbedder | None
    _huggingface: HuggingFaceEmbedder | None
    _initialized: bool

    async def _cache_key(self, prefix: str, text: str) -> str:
        raise NotImplementedError

    async def _cache_get(self, key: str) -> list[float] | None:
        raise NotImplementedError

    async def _cache_set(self, key: str, vector: list[float], ttl: int = 86400) -> None:
        raise NotImplementedError

    async def initialize(self) -> None:
        """Initialize embedding providers."""
        import os

        from .config import HF_TOKEN, OPENROUTER_KEY

        or_key = os.environ.get("OPENROUTER_API_KEY", "") or OPENROUTER_KEY
        hf_token = os.environ.get("HUGGINGFACE_TOKEN", "") or HF_TOKEN

        # PRIMARY embedding provider
        self._qwen = Qwen3Embedder()
        logger.info("CryptoEmbedder: primary qwen3 embedder ready")

        logger.info("CryptoEmbedder: local fallback embedder ready")

        if or_key:
            self._openrouter = OpenRouterEmbedder(api_key=or_key)
            logger.info("CryptoEmbedder: OpenRouter embedder ready")

        if hf_token:
            self._huggingface = HuggingFaceEmbedder(token=hf_token)
            logger.info("CryptoEmbedder: HuggingFace embedder ready")

        self._initialized = True
        logger.info("CryptoEmbedder initialized")

    async def _semantic_embed(self, texts: list[str], head: str = "semantic") -> list[list[float]]:
        """
        Semantic embedding pipeline with tiered fallback:
        PRIMARY:   Primary embedder (Qwen3-Embedding-8B (llama.cpp))
        FALLBACK:  OpenRouter or API-based embedder
        FALLBACK:  OpenRouter/HF embedder (external fallback)
        FALLBACK:  HuggingFace embedder
        LAST:      Hash-based (low quality, deterministic)
        """
        if not texts:
            return []

        # PRIMARY embedder
        if self._qwen:
            try:
                return await self._qwen.embed(texts)
            except Exception as e:
                logger.warning("Primary int embedding failed: %s", e)

        # Fallback: OpenRouter
        if self._openrouter:
            try:
                return await self._openrouter.embed(texts)
            except Exception as e:
                logger.warning("OpenRouter embedding failed: %s", e)

        # Fallback: OpenRouter embedder
        if self._openrouter:
            try:
                return await self._openrouter.embed(texts)
            except Exception as e:
                logger.warning("OpenRouter fallback embedding failed: %s", e)

        # Fallback: HuggingFace
        if self._huggingface:
            try:
                return await self._huggingface.embed(texts)
            except Exception as e:
                logger.warning("HuggingFace embedding failed: %s", e)

        # TOP-100 #23: fail loud — hash vectors poison retrieval silently.
        raise RuntimeError("CryptoEmbedder: all embedding providers failed (local/openrouter/hf)")

    async def _semantic_embed_one(self, text: str, head: str = "semantic") -> list[float]:
        # Check cache first
        cache_key = await self._cache_key(head, text)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        results = await self._semantic_embed([text], head)
        if not results:
            raise RuntimeError(f"CryptoEmbedder: no embedding produced for head={head}")
        vector: list[float] = results[0]

        # Store in cache (24h TTL for semantic embeddings)
        await self._cache_set(cache_key, vector, ttl=86400)
        return vector

    async def embed_contract(
        self,
        address: str,
        code: str,
        chain: str = "ethereum",
        verified: bool = False,
    ) -> Any:
        """Full contract audit embedding."""
        return await ContractCodeEmbedder.embed(
            code,
            semantic_embedder=self._openrouter,
        )

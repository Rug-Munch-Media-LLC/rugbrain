"""Canonical query embedding for search and eval.

This module is the single working query-embedding path:
Qwen3-Embedding-8B via ``rugbrain.embedder`` (DeepInfra canonical primary +
local llama.cpp :8004 fallback — same model, same 4096d space). One function,
no state, no stubs.

The ``crypto_embeddings_pkg`` singleton it replaced is a separate package
that is NOT reached from here; see that package's own config for its status.
"""

from __future__ import annotations


async def embed_query(text: str) -> list[float]:
    """Embed one query string with the canonical Qwen3-Embedding-8B path."""
    from rugbrain.embedder import get_embedder

    return await get_embedder().embed_one(text)


class _CanonicalEmbedder:
    """Adapter for the retired ``crypto_embeddings`` embedder interface.

    Call sites across ``search/`` were written against an object with
    ``embed_query`` / ``embed_one`` / ``embed``; the legacy singleton that
    provided them is a stub. This adapter serves those methods from the one
    working path, so no call site needs rewriting.
    """

    async def embed_query(self, text: str) -> list[float]:
        return await embed_query(text)

    async def embed_one(self, text: str) -> list[float]:
        return await embed_query(text)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [await embed_query(text) for text in texts]


async def get_embedder() -> _CanonicalEmbedder:
    """Async factory matching the legacy ``get_embedder()`` signature."""
    return _CanonicalEmbedder()


__all__ = ["embed_query", "get_embedder"]

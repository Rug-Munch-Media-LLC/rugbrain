"""Canonical query embedding for search and eval.

The legacy ``crypto_embeddings_pkg`` singleton is a retired stub — every head
raises ``NotImplementedError`` (2026-09-05 retirement), so anything that embeds
queries through it is dead: the eval harness retriever and the search filter
path both were.

This module is the single working path: Qwen3-Embedding-4B via
``rugbrain.embedder`` (DeepInfra canonical primary + local llama.cpp :8004
fallback — same model, same 2560d space). One function, no state, no stubs.
"""

from __future__ import annotations


async def embed_query(text: str) -> list[float]:
    """Embed one query string with the canonical Qwen3-Embedding-4B path."""
    from rugbrain.embedder import get_embedder

    return await get_embedder().embed_one(text)


__all__ = ["embed_query"]

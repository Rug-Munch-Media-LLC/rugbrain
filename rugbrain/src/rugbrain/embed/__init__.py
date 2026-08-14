"""Embedding backends (Infinity-compatible HTTP + legacy bge-m3) and cache."""

from __future__ import annotations

from rugbrain.embed.embedder import (
    BGE_M3_Embedder,
    CachedEmbedder,
    EmbedderProtocol,
    FallbackEmbedder,
    InfinityEmbedder,
    get_default_embedder,
    get_embedder,
    lookup_dim,
    normalize_dim,
    shutdown_embedder,
)
from rugbrain.embed.embeddings import (
    EMBEDDING_DIM,
    RAGUnavailableError,
    create_embedder,
    current_backend,
    get_embedding,
    get_embeddings,
)

__all__ = [
    "BGE_M3_Embedder",
    "CachedEmbedder",
    "EMBEDDING_DIM",
    "EmbedderProtocol",
    "FallbackEmbedder",
    "InfinityEmbedder",
    "RAGUnavailableError",
    "create_embedder",
    "current_backend",
    "get_default_embedder",
    "get_embedder",
    "get_embedding",
    "get_embeddings",
    "lookup_dim",
    "normalize_dim",
    "shutdown_embedder",
]

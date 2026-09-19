"""Crypto Embedder — multi-head embedding system for RAG.

Architecture:
  PRIMARY:   Configurable primary model (set via RAG_PRIMARY_EMBED_MODEL)
  FALLBACK:  Configurable fallback model (set via RAG_FALLBACK_EMBED_MODEL)
  SPECIALTY: Crypto-aware hashing - contract bytecode sim, tx pattern fingerprints

Embedding Heads:
  SEMANTIC    - token descriptions, scam narratives, news
  CODE        - smart contract similarity (AST-aware + bytecode hash)
  BEHAVIORAL  - transaction patterns, wallet behavior vectors
  ENTITY      - wallet labels, cluster IDs, known scammer fingerprints

Collections:
  Configured via RAG_COLLECTIONS env var (comma-separated).

This package is a behavior-identical split of the former
``crypto_embeddings.py`` module (818 lines). The public API is re-exported
here verbatim so existing ``from rugbrain.crypto_embeddings import ...``
call sites keep working unchanged.
"""

from __future__ import annotations

from .config import (
    COLLECTIONS as COLLECTIONS,
)
from .config import (
    DIMS as DIMS,
)
from .config import (
    HEAD_DEFAULTS as HEAD_DEFAULTS,
)
from .config import (
    HF_TOKEN as HF_TOKEN,
)
from .config import (
    NVIDIA_EMBED_URL as NVIDIA_EMBED_URL,
)
from .config import (
    OPENROUTER_KEY as OPENROUTER_KEY,
)
from .config import (
    OR_FALLBACK_EMBED as OR_FALLBACK_EMBED,
)
from .config import (
    OR_PAID_FALLBACK as OR_PAID_FALLBACK,
)
from .config import (
    OR_PRIMARY_EMBED as OR_PRIMARY_EMBED,
)
from .config import (
    REDIS_HOST as REDIS_HOST,
)
from .config import (
    REDIS_PORT as REDIS_PORT,
)
from .config import (
    REDIS_PW as REDIS_PW,
)
from .core import CryptoEmbedder as CryptoEmbedder
from .patterns import KNOWN_SCAM_PATTERNS as KNOWN_SCAM_PATTERNS
from .result import EmbeddingResult as EmbeddingResult
from .singleton import _embedder as _embedder
from .singleton import get_embedder as get_embedder

__all__ = [
    "COLLECTIONS",
    "DIMS",
    "HEAD_DEFAULTS",
    "HF_TOKEN",
    "KNOWN_SCAM_PATTERNS",
    "NVIDIA_EMBED_URL",
    "OPENROUTER_KEY",
    "OR_FALLBACK_EMBED",
    "OR_PAID_FALLBACK",
    "OR_PRIMARY_EMBED",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_PW",
    "CryptoEmbedder",
    "EmbeddingResult",
    "get_embedder",
]

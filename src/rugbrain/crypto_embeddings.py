"""Crypto Embedder — compatibility shim.

The implementation was split into the :mod:`rugbrain.crypto_embeddings_pkg`
package (behavior-identical, ≤200-line modules). This module re-exports the
public API so existing ``from rugbrain.crypto_embeddings import ...`` call
sites keep working unchanged.
"""

from __future__ import annotations

from rugbrain.crypto_embeddings_pkg import (
    COLLECTIONS as COLLECTIONS,
)
from rugbrain.crypto_embeddings_pkg import (
    DIMS as DIMS,
)
from rugbrain.crypto_embeddings_pkg import (
    HEAD_DEFAULTS as HEAD_DEFAULTS,
)
from rugbrain.crypto_embeddings_pkg import (
    HF_TOKEN as HF_TOKEN,
)
from rugbrain.crypto_embeddings_pkg import (
    KNOWN_SCAM_PATTERNS as KNOWN_SCAM_PATTERNS,
)
from rugbrain.crypto_embeddings_pkg import (
    NVIDIA_EMBED_URL as NVIDIA_EMBED_URL,
)
from rugbrain.crypto_embeddings_pkg import (
    OPENROUTER_KEY as OPENROUTER_KEY,
)
from rugbrain.crypto_embeddings_pkg import (
    OR_FALLBACK_EMBED as OR_FALLBACK_EMBED,
)
from rugbrain.crypto_embeddings_pkg import (
    OR_PAID_FALLBACK as OR_PAID_FALLBACK,
)
from rugbrain.crypto_embeddings_pkg import (
    OR_PRIMARY_EMBED as OR_PRIMARY_EMBED,
)
from rugbrain.crypto_embeddings_pkg import (
    REDIS_HOST as REDIS_HOST,
)
from rugbrain.crypto_embeddings_pkg import (
    REDIS_PORT as REDIS_PORT,
)
from rugbrain.crypto_embeddings_pkg import (
    REDIS_PW as REDIS_PW,
)
from rugbrain.crypto_embeddings_pkg import (
    CryptoEmbedder as CryptoEmbedder,
)
from rugbrain.crypto_embeddings_pkg import (
    EmbeddingResult as EmbeddingResult,
)
from rugbrain.crypto_embeddings_pkg import (
    _embedder as _embedder,
)
from rugbrain.crypto_embeddings_pkg import (
    get_embedder as get_embedder,
)

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

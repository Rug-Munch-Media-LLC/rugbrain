"""CryptoEmbedder — the single multi-head embedder class.

The class is assembled from focused mixins; method resolution is unchanged
from the former monolithic module (all methods live on one class).
"""

from __future__ import annotations

from typing import Any

from .cache import CacheMixin
from .config import COLLECTIONS
from .heads import HeadsMixin
from .providers import ProviderMixin
from .similarity import SimilarityMixin
from .store import StoreMixin


class CryptoEmbedder(
    ProviderMixin,
    CacheMixin,
    HeadsMixin,
    SimilarityMixin,
    StoreMixin,
):
    """
    Ultimate multi-head embedder for crypto scam detection.

    Usage:
        embedder = CryptoEmbedder()
        await embedder.initialize()

        # Token scam analysis
        vec = await embedder.embed_token_scam(
            name="FakeRugToken",
            symbol="RUG",
            description="Revolutionary defi...",
            contract_code="pragma solidity...",
            creator_address="0x...",
        )

        # Wallet investigation
        vec = await embedder.embed_wallet(
            address="0x...",
            transactions=[...],
            labels=["suspected_scammer"],
        )

        # Semantic search
        results = await embedder.search("honeypot tokens with ownership renounce pattern")
    """

    def __init__(self) -> None:
        self._qwen: Any = None
        self._openrouter: Any = None
        self._huggingface: Any = None
        self._redis: Any = None
        self._initialized = False
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "initialized": self._initialized,
            "providers": {
                "primary": self._qwen is not None,
                "fallback_api": self._openrouter is not None,
                "qwen": self._qwen is not None,
                "huggingface": self._huggingface is not None,
            },
            "cache": {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "ratio": round(self._cache_hits / max(1, self._cache_hits + self._cache_misses), 3),
            },
            "collections_count": len(COLLECTIONS),
        }

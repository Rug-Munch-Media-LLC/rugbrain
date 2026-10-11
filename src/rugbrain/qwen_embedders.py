#!/usr/bin/env python3
"""
Embedding provider classes — facade over :mod:`rugbrain.embedder_providers`.

One job per module; this file only re-exports so every existing caller
(``from rugbrain.qwen_embedders import Qwen3Embedder``) keeps working:

* :mod:`~rugbrain.embedder_providers.qwen3` — Qwen3-Embedding-8B, the
  canonical corpus lane (DeepInfra hosted primary, llama.cpp local fallback).
* :mod:`~rugbrain.embedder_providers.external` — OpenRouter + HuggingFace
  fallbacks (different dim; never used to write corpus vectors).
* :mod:`~rugbrain.embedder_providers.contract` — 320-dim structural contract
  embedder.

The re-exports below are declared in ``__all__`` on purpose: without it
``ruff --fix`` deletes them as unused imports.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from rugbrain.embedder_providers.contract import embed_contract_code
from rugbrain.embedder_providers.external import (
    HF_TOKEN,
    OPENROUTER_KEY,
    OR_FALLBACK_EMBED,
    OR_PRIMARY_EMBED,
    HuggingFaceEmbedder,
    OpenRouterEmbedder,
)
from rugbrain.embedder_providers.qwen3 import (
    DEEPINFRA_API_KEY,
    DEEPINFRA_EMBED_MODEL,
    DEEPINFRA_EMBED_URL,
    Qwen3Embedder,
)

logger = logging.getLogger(__name__)


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
        return await embed_contract_code(code, semantic_embedder)


__all__ = [
    "DEEPINFRA_API_KEY",
    "DEEPINFRA_EMBED_MODEL",
    "DEEPINFRA_EMBED_URL",
    "HF_TOKEN",
    "OPENROUTER_KEY",
    "OR_FALLBACK_EMBED",
    "OR_PRIMARY_EMBED",
    "ContractCodeEmbedder",
    "HuggingFaceEmbedder",
    "OpenRouterEmbedder",
    "Qwen3Embedder",
    "embed_contract_code",
]

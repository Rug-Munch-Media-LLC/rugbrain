"""Structural contract embedder — 320-dim vector over decompiled contract code.

128 dims of local structural features (rug-pattern surface) + 192 dims of
semantic understanding, so obfuscated code still lands near its readable twin.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from rugbrain.feature_extractors import extract_contract_features

logger = logging.getLogger(__name__)

_SEMANTIC_DIMS = 192


async def embed_contract_code(
    code: str, semantic_embedder: Any = None
) -> np.ndarray[Any, np.dtype[np.float32]]:
    """128-dim structural features + 192-dim semantic (zeros when unavailable)."""
    structural = extract_contract_features(code)
    return np.concatenate([structural, await _semantic_half(code, semantic_embedder)])


async def _semantic_half(code: str, semantic_embedder: Any) -> np.ndarray[Any, np.dtype]:
    """Semantic 192-dim half, or zeros. Never raises — a bad embed is not a failed scan."""
    empty = np.zeros(_SEMANTIC_DIMS, dtype=np.float32)
    if not semantic_embedder or len(code) <= 50:
        return empty
    try:
        # Drop the meaningful parts, not just imports/boilerplate: 4000 chars is
        # enough for the key logic, 3000 is the embedder's own truncation.
        vec = await semantic_embedder.embed_one(f"Smart contract code: {code[:3000]}")
        semantic = np.array(vec[:_SEMANTIC_DIMS], dtype=np.float32)
    except Exception as exc:
        logger.warning("Semantic contract embedding failed: %s", exc)
        return empty
    if len(semantic) < _SEMANTIC_DIMS:
        semantic = np.pad(semantic, (0, _SEMANTIC_DIMS - len(semantic)))
    return semantic

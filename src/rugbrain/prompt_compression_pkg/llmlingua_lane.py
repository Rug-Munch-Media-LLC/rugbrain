"""The optional LLMLingua lane (lazy import, loaded at most once).

Moved verbatim from ``prompt_compression.py``. ``LLMLINGUA_AVAILABLE`` is
probed here; the public module keeps its own probe so a test that patches
``rugbrain.prompt_compression.LLMLINGUA_AVAILABLE`` still disables the lane
before it is ever consulted.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
from typing import Any

from rugbrain.prompt_compression_pkg.signals import _estimate_tokens

logger = logging.getLogger(__name__)

# ── llmlingua availability (probed once; model loads lazily) ──────────────

LLMLINGUA_MODEL = os.getenv(
    "LLMLINGUA_MODEL", "microsoft/llmlingua-2-xlm-roberta-large-meeting-bank"
)
LLMLINGUA_AVAILABLE = importlib.util.find_spec("llmlingua") is not None

_LLMLINGUA_PROBED = False
_LLMLINGUA_COMPRESSOR: Any | None = None


def _get_llmlingua_compressor(model_name: str | None) -> Any | None:
    """Lazily create the LLMLingua ``PromptCompressor`` singleton (or ``None``)."""
    global _LLMLINGUA_PROBED, _LLMLINGUA_COMPRESSOR
    if not LLMLINGUA_AVAILABLE:
        return None
    if _LLMLINGUA_PROBED:
        return _LLMLINGUA_COMPRESSOR
    _LLMLINGUA_PROBED = True
    try:
        from llmlingua import (
            PromptCompressor,
        )  # optional dependency, guarded by LLMLINGUA_AVAILABLE

        _LLMLINGUA_COMPRESSOR = PromptCompressor(model_name=model_name or LLMLINGUA_MODEL)
        logger.info(
            "prompt_compression: llmlingua compressor loaded (%s)",
            model_name or LLMLINGUA_MODEL,
        )
    except Exception as exc:
        logger.warning("prompt_compression: llmlingua load failed (%s) — extractive fallback", exc)
        _LLMLINGUA_COMPRESSOR = None
    return _LLMLINGUA_COMPRESSOR


def _llmlingua_compress(
    text: str,
    budget_tokens: int,
    query: str | None,
    model_name: str | None,
) -> str:
    """Compress *text* with llmlingua's sync ``compress_prompt`` API."""
    compressor = _get_llmlingua_compressor(model_name)
    if compressor is None:
        raise RuntimeError("llmlingua unavailable")

    params = inspect.signature(compressor.compress_prompt).parameters
    kwargs: dict[str, Any] = {"context": text, "question": query or ""}
    if "target_token" in params:
        result = compressor.compress_prompt(**kwargs, target_token=max(1, budget_tokens))
    else:  # older llmlingua: rate-based compression
        rate = min(1.0, budget_tokens / max(1, _estimate_tokens(text)))
        result = compressor.compress_prompt(**kwargs, rate=max(0.05, rate))

    if isinstance(result, dict):
        out = result.get("compressed_prompt")
        if isinstance(out, str) and out:
            return out
    return text

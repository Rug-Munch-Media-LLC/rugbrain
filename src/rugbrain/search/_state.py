"""
RAG Search — shared state (leaf).

The canonical state lives on the facade module (``rugbrain.search_service``) for
back-compat. This module resolves it lazily via PEP 562 ``__getattr__`` instead
of importing the facade at module import time — that import used to close the
cycle ``search_service -> search -> search._state -> search_service``.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_FACADE_STATE = ("_get_redis", "_pattern_cache", "_pattern_cache_loaded", "_seeded")

__all__ = ["_get_redis", "_pattern_cache", "_pattern_cache_loaded", "_seeded"]


def __getattr__(name: str) -> Any:
    """Resolve facade-owned state lazily (single source of truth, no import edge)."""
    if name in _FACADE_STATE:
        from rugbrain import search_service as _facade

        return getattr(_facade, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

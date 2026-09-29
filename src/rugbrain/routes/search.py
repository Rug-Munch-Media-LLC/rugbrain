"""Search routes: ``/search``, ``/search/multi`` + the ``scam_check_router``.

Stable facade over :mod:`rugbrain.routes.search_pkg`. The implementation was
split one job per module (see that package's docstring); this module exists so
every existing import site — ``endpoints.py``, the route tests, anything
reaching ``rugbrain.routes.search`` — keeps working unchanged.

No URL prefix on ``router`` — the parent router in ``endpoints.py`` supplies
``/api/v1``. ``scam_check_router`` keeps its own ``/api/v1/rag/v2`` prefix and
is mounted separately by ``main.py`` (via the ``rugbrain.endpoints``
re-export), exactly as before.
"""

from __future__ import annotations

from rugbrain.routes.search_pkg import (
    MultiSearchRequest,
    router,
    scam_check,
    scam_check_router,
    search_crypto_code_endpoint,
    search_multi,
    search_threat_intel,
)

__all__ = [
    "MultiSearchRequest",
    "router",
    "scam_check",
    "scam_check_router",
    "search_crypto_code_endpoint",
    "search_multi",
    "search_threat_intel",
]

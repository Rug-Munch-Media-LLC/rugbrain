"""The search HTTP surface, split one endpoint per module.

- :mod:`threat` — ``GET /search`` semantic search over scanner findings
- :mod:`multi`  — ``POST /search/multi`` multi-collection RRF fusion
- :mod:`code`   — ``GET /search/crypto-code`` public code search
- :mod:`scam`   — ``scam_check_router`` address scam check

Shared request models, the peer-IP rate limiter, and the public-collection
allowlist live in :mod:`_shared`. Retrieval, ratings, chain-data, and
reflection are separate modules under ``routes/`` — see ``search_retrieval``,
``search_postprocess``, and ``search_chain_data``.

No URL prefix on ``router`` — the parent router in ``endpoints.py`` supplies
``/api/v1``. ``scam_check_router`` keeps its own ``/api/v1/rag/v2`` prefix and
is mounted separately by ``main.py`` (via the ``rugbrain.endpoints``
re-export), exactly as before.
"""

from __future__ import annotations

from fastapi import APIRouter

from rugbrain.routes.search_pkg import code, multi, scam, threat
from rugbrain.routes.search_pkg._shared import MultiSearchRequest

#: Aggregated search router. Operation ids are pinned to their historical
#: values so the generated OpenAPI document does not change.
router = APIRouter(tags=["rag-optimization"])
router.include_router(threat.router)
router.include_router(multi.router)
router.include_router(code.router)

#: Mounted separately by main.py under its own prefix.
scam_check_router = scam.scam_check_router

search_threat_intel = threat.search_threat_intel
search_multi = multi.search_multi
search_crypto_code_endpoint = code.search_crypto_code_endpoint
scam_check = scam.scam_check

__all__ = [
    "MultiSearchRequest",
    "router",
    "scam_check",
    "scam_check_router",
    "search_crypto_code_endpoint",
    "search_multi",
    "search_threat_intel",
]

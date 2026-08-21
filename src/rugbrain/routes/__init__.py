"""Route submodules split out of ``rugbrain/endpoints.py`` (degodding phase 3).

Each module defines an ``APIRouter`` WITHOUT a URL prefix — the parent
router in ``rugbrain/endpoints.py`` carries ``prefix="/api/v1"`` and
includes these sub-routers, so the final URL paths and auth behavior are
byte-identical to the pre-split god module. The ``scam_check_router``
(prefix ``/api/v1/rag/v2``) is defined in ``routes/search.py`` and
re-exported from ``rugbrain/endpoints.py``.
"""

from __future__ import annotations

from rugbrain.routes import analyze, core, ingest, intel, monitors, rag_tools, search

__all__ = ["analyze", "core", "ingest", "intel", "monitors", "rag_tools", "search"]

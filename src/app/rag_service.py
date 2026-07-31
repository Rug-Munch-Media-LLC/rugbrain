"""RAG service shim for the rmi-rag ``app`` compatibility layer.

rmi-backend code (and some rmi-rag modules) import
``from app.rag_service import three_pillar_search``. This module
delegates to rmi-rag's own three-pillar hybrid search engine so the
import resolves inside the standalone container.
"""

from __future__ import annotations

from rmi_rag.search.engine import three_pillar_search

__all__ = ["three_pillar_search"]

"""
RAG Search — shared state.

Shared Redis connection + module-level state.

IMPORTS shared state from the facade (search_service) to ensure a
single source of truth and avoid circular-import deadlocks.

Consumers: engine.py, filters.py
"""

import logging

logger = logging.getLogger(__name__)

# ── Shared state imported from facade ──
# The facade (rmi_rag.search_service) MUST define these BEFORE
# any module-level code in this package tries to access them.
# This import reads pre-existing attributes from the facade module,
# which is already in sys.modules with these attrs set.
from rmi_rag.search_service import (  # noqa: E402, F401
    _get_redis,
    _pattern_cache,
    _pattern_cache_loaded,
    _seeded,
)

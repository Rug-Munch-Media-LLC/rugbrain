"""Lightweight logging shim for the rmi-rag ``app`` compatibility layer.

rmi-backend's ``app.core.logging`` returns structlog bound loggers and
supports ``logger.info("event", key=value)``. The rmi-rag container does
not ship structlog, so this module provides a stdlib-based ``get_logger``
that is API-compatible for the call patterns used by the ported modules
(``logger.debug("fmt %s", arg)``, ``logger.warning("msg")``).

If structlog is installed we use it (best effort); otherwise fall back
to stdlib logging.
"""

from __future__ import annotations

import logging

try:  # pragma: no cover - best-effort
    import structlog  # type: ignore[import-untyped]

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover
    _HAS_STRUCTLOG = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger for *name*.

    Returns a structlog bound logger when structlog is available, else a
    stdlib ``logging.Logger``. Both support the call patterns used across
    the rmi-rag API surface.
    """
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)  # type: ignore[no-any-return]
    return logging.getLogger(name)

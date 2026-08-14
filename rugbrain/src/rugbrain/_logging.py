"""Minimal structured logger for RugBrain (stdlib-backed).

The ported modules call ``log.info("event", key=value)`` (structlog-style
keyword logging) and occasionally printf-style ``log.warning("fmt %s", arg)``.
This module provides a tiny ``get_logger`` that supports both call styles on
top of the standard library — no external dependency, no internal coupling.

Behaviour matches the private monorepo's ``app.core.logging.get_logger`` for
the call surface the RAG modules actually use (``debug``/``info``/``warning``/
``error``/``critical``, ``exc_info``, ``extra``). Keyword arguments are
rendered as ``key=value`` pairs appended to the event message.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_configured = False


class _KwargLogger:
    """Log adapter that accepts both structlog-style kwargs and printf args."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._logger = logging.getLogger(name)

    def _emit(self, level: int, event: str, *args: Any, **kwargs: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return
        exc_info = kwargs.pop("exc_info", None)
        extra = kwargs.pop("extra", None)
        if args:
            try:
                event = event % args
            except TypeError:  # pragma: no cover - defensive
                event = f"{event} {' '.join(map(str, args))}"
        if kwargs:
            pairs = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
            event = f"{event} {pairs}"
        self._logger.log(level, event, exc_info=exc_info, extra=extra)

    def debug(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, event, *args, **kwargs)

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.INFO, event, *args, **kwargs)

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.WARNING, event, *args, **kwargs)

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.ERROR, event, *args, **kwargs)

    def critical(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.CRITICAL, event, *args, **kwargs)


def get_logger(name: str) -> _KwargLogger:
    """Return a structured logger for *name* (stdlib-backed, kwargs-capable)."""
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root = logging.getLogger("rugbrain")
        root.addHandler(handler)
        if root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        root.propagate = False
        _configured = True
    return _KwargLogger(name)


__all__ = ["get_logger"]

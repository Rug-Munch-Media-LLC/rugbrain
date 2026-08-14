"""OpenInference spans; OTLP endpoint via env only, off by default.

TODO(rugbrain-extract): port this module from app/rag/tracing.py into this location.
Until ported, importing it raises NotImplementedError.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"rugbrain module {__name__!r} is a scaffold stub. "
        f"Port app/rag/tracing.py first (see MIGRATION.md step 2/3)."
    )


__all__: list[str] = []

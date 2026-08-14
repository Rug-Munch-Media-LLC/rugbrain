"""Token-bucket ingestion rate limiter.

TODO(rugbrain-extract): port this module from app/rag/rate_limiter.py into this location.
Until ported, importing it raises NotImplementedError.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"rugbrain module {__name__!r} is a scaffold stub. "
        f"Port app/rag/rate_limiter.py first (see MIGRATION.md step 2/3)."
    )


__all__: list[str] = []

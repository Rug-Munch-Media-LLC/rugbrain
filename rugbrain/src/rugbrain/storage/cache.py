"""CacheBackend adapter: Redis implementation + in-memory fallback (replaces app.core.redis).

TODO(rugbrain-extract): implement this adapter from scratch (no private source).
Until ported, importing it raises NotImplementedError.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"rugbrain module {__name__!r} is a scaffold stub. "
        f"Port None first (see MIGRATION.md step 2/3)."
    )


__all__: list[str] = []

"""One envelope for every retrieval-layer adapter.

``{"ok", "data", "source", "error": {"code", "detail"}, "fallback"}``

A refused layer never returns ``data``: the caller sees ``ok=False`` plus the
typed ``code`` and the ``fallback`` layer that can still answer.
"""

from __future__ import annotations

from typing import Any


def layer_response(
    *,
    source: str,
    data: Any = None,
    error_code: str | None = None,
    detail: str = "",
    fallback: str | None = None,
) -> dict[str, Any]:
    """Build the shared adapter envelope (success or typed refusal)."""
    ok = error_code is None
    return {
        "ok": ok,
        "data": data if ok else None,
        "source": source,
        "error": None if ok else {"code": error_code, "detail": detail},
        "fallback": None if ok else fallback,
    }

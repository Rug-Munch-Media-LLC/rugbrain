"""delegate_lookup — the delegation door for exact and symbol questions.

Wraps :func:`rugbrain.fleet_layers.delegate.delegate` so an assistant never has
to remember which app owns which question: RugBrain calls zoekt / serena, keeps
its own search as a typed fallback, and always reports the answering ``source``.
"""

from __future__ import annotations

from typing import Any

from rugbrain.fleet_layers.delegate import delegate
from rugbrain.rugbrain_mcp.app import mcp

KINDS = ("exact", "symbol")
PROJECT_HINT = "symbol lookups need the absolute repo path, e.g. /srv/work/repos/rugbrain"


def _refuse(code: str, detail: str) -> dict[str, Any]:
    """Typed refusal that never carries data."""
    return {"status": "refused", "tool": "delegate_lookup", "source": "delegate",
            "data": None, "error": {"code": code, "detail": detail}, "fallback": PROJECT_HINT}


@mcp.tool(name="delegate_lookup")
def delegate_lookup(kind: str, query: str, project: str | None = None) -> dict[str, Any]:
    """Answer an exact-string or symbol question via the app that owns it.

    Args:
        kind: "exact" (zoekt owns it) or "symbol" (serena owns it).
        query: Literal/regex for exact, or a symbol name path for symbol.
        project: Absolute repo path; required for kind="symbol".
    """
    if kind not in KINDS:
        return _refuse("unsupported_kind", f"kind={kind!r}; use one of {KINDS}")
    if kind == "symbol" and not project:
        return _refuse("project_not_activated", PROJECT_HINT)
    raw = delegate(kind, query=query, project=project or "")
    ok = bool(raw.get("ok")) and raw.get("data") is not None
    return {
        "status": "ok" if ok else "refused",
        "tool": "delegate_lookup",
        "source": raw.get("source"),
        "delegated_to": raw.get("delegated_to"),
        "fell_back_from": raw.get("fell_back_from"),
        "data": raw.get("data") if ok else None,
        "error": None if ok else (raw.get("error") or {"code": "bad_response", "detail": "no data"}),
        "fallback": None if ok else raw.get("fallback"),
    }

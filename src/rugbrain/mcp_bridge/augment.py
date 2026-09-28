"""One-call code-query entry point: ``augment_code_query``.

Runs ``search_codebase`` + ``get_security_context`` and merges the results
so the RAG can produce a code-first answer with security context without
managing the two bridge calls itself.
"""

from __future__ import annotations

from typing import Any


async def augment_code_query(
    query: str, *, limit: int = 8, include_raw: bool = False
) -> dict[str, Any]:
    """One-call entry point for code-related RAG queries.

    Runs ``search_codebase`` + ``get_security_context`` and merges the
    results so the RAG can produce a code-first answer with security context
    without managing the two bridge calls itself. Falls back to RAG-only
    knowledge per sub-call when the MCP is down.

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback" | "mixed"),
          "query", "results", "security_patterns", "total",
          "raw": {...} (only when include_raw=True),
          "guard": {...},
        }

    Guarded: the query is sanitized once here; sub-calls refuse before
    dispatch when nothing usable remains. Aggregated ``guard`` metadata is
    attached to the merged response.

    ``include_raw`` is OFF by default. The merged ``raw`` block used to
    re-echo the full ``search_codebase`` payload — a second verbatim copy of
    ``results`` plus a third copy of the security patterns, on top of the
    normalization the sub-calls had already done.
    """
    from rugbrain import mcp_bridge as _pkg

    safe_query, guard = _pkg._guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "augment_code_query",
            "query": query,
            "results": [],
            "security_patterns": [],
            "total": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    search = await _pkg.search_codebase(safe_query, limit=limit, include_raw=include_raw)
    security = await _pkg.get_security_context(safe_query, top_k=min(limit, 5))

    bridges = {search.get("bridge"), security.get("bridge")}
    if bridges == {"mcp"}:
        bridge = "mcp"
    elif bridges == {"rag_fallback"}:
        bridge = "rag_fallback"
    else:
        bridge = "mixed"

    results = list(search.get("results", []))
    security_results = list(security.get("results", []))
    agg_guard = {
        **guard,
        "flagged": guard["flagged"]
        or bool(search.get("guard", {}).get("flagged"))
        or bool(security.get("guard", {}).get("flagged")),
        "refused": guard["refused"]
        or bool(search.get("guard", {}).get("refused"))
        or bool(security.get("guard", {}).get("refused")),
    }
    payload: dict[str, Any] = {
        "ok": bool(search.get("ok")) or bool(security.get("ok")),
        "bridge": bridge,
        "tool": "augment_code_query",
        "query": query,
        "results": results,
        "security_patterns": security_results,
        "total": len(results) + len(security_results),
        "guard": agg_guard,
    }
    if include_raw:
        payload["raw"] = {
            "search": search.get("raw") if search.get("bridge") == "mcp" else None,
            "security": security.get("results") if security.get("bridge") == "mcp" else None,
        }
    return payload

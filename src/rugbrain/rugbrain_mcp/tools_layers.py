"""Retrieval-layer tools: exact zoekt search, serena symbols, LLMLingua compression.

Each tool wraps ONE adapter from :mod:`rugbrain.fleet_layers` and re-shapes the
adapter envelope into the MCP surface contract
``{"status": "ok"|"refused", "tool", "source", "data", "error", "fallback"}``.
Refusals are typed (``timeout``, ``unavailable``, ``project_not_activated`` …)
and never carry ``data``: a dead layer can never look like an empty answer.
"""

from __future__ import annotations

from typing import Any

from rugbrain.fleet_layers.compress import compress
from rugbrain.fleet_layers.serena import serena_symbols
from rugbrain.fleet_layers.zoekt import zoekt_search
from rugbrain.rugbrain_mcp.app import mcp

PROJECT_HINT = "serena answers only inside an activated project — pass the absolute repo path, e.g. /srv/work/repos/rugbrain"


def _envelope(tool: str, source: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Map an adapter envelope onto the MCP contract; never a silent empty success."""
    error = raw.get("error") or None
    ok = bool(raw.get("ok")) and raw.get("data") is not None
    if raw.get("ok") and raw.get("data") is None and error is None:
        ok = False
        error = {"code": "bad_response", "detail": "adapter reported ok without data"}
    return {
        "status": "ok" if ok else "refused",
        "tool": tool,
        "source": source,
        "data": raw.get("data") if ok else None,
        "error": None if ok else error,
        "fallback": None if ok else raw.get("fallback"),
    }


@mcp.tool(name="exact_search")
def exact_search(pattern: str, files_only: bool = False) -> dict[str, Any]:
    """Exact string / regex search over the COMMITTED corpus (zoekt, :6070).

    Args:
        pattern: Literal string or regex, e.g. "def exact_search".
        files_only: Skip chunk content and return file/repo rows only.

    Uncommitted work is invisible here — use rg-search for the working tree.
    """
    return _envelope("exact_search", "zoekt", zoekt_search(pattern, files_only=files_only))


@mcp.tool(name="symbol_info")
def symbol_info(name_path_pattern: str, project: str | None = None) -> dict[str, Any]:
    """LSP symbol lookup via serena (:9092) — activates the project first.

    Args:
        name_path_pattern: Symbol name path, e.g. "RugBrainClient.search".
        project: Absolute path of the repo to activate. Required.
    """
    if not project:
        return {
            "status": "refused",
            "tool": "symbol_info",
            "source": "serena",
            "data": None,
            "error": {"code": "project_not_activated", "detail": f"project is required; {PROJECT_HINT}"},
            "fallback": PROJECT_HINT,
        }
    return _envelope("symbol_info", "serena", serena_symbols(project, name_path_pattern))


@mcp.tool(name="compress_text")
def compress_text(text: str, ratio: float = 0.4) -> dict[str, Any]:
    """Compress agent-facing prose with LLMLingua (:8077); reports ratio_actual.

    Args:
        text: Prose to compress. Code/identifiers are NOT safe to compress.
        ratio: Requested output/input ratio (default 0.4).
    """
    return _envelope("compress_text", "cc-compress", compress(text, ratio))

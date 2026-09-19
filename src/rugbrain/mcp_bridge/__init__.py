"""RugBrain ↔ RMI Codebase MCP bridge (package).

Lets the RAG answer code-related queries with deeper answers from the local
RMI Codebase MCP (FastMCP over stdio).

Bridge functions:
    search_codebase(query)            → MCP ``rmi_search()``
    trace_codebase(function)          → MCP ``rmi_trace()``
    get_security_context(query)       → MCP ``query_security_context()``
    resolve_library(query)            → MCP ``resolve_library()`` (Context7-style)
    query_codebase(library_id, query) → MCP ``query_codebase()`` (Context7-style)
    sync_security_patterns(limit)     → pull MCP security patterns into RAG
    augment_code_query(query)         → search + security context in one call
    mcp_status() / mcp_available()    → liveness probes

Execution strategy (in order): in-process direct import → subprocess runner
(``scripts/mcp_tool_runner.py``) → RAG fallback using RugBrain's own
coding/security knowledge.

Security guardrails (additive, see rugbrain.guardrails):
  * ``sanitize_query`` runs on every user query before it is sent to the
    MCP (or the RAG fallback). Prompt-injection queries are neutralized
    (stripped) or refused outright when nothing usable remains — the
    response carries a ``guard`` metadata dict (``flagged``, ``reasons``,
    ``sanitized``, ``refused``).
  * ``redact_output`` strips secret patterns (private keys, mnemonics,
    API keys, internal hostnames, private IPs) from result content before
    it is returned to callers. Guardrails never raise — a guardrail
    failure degrades to passthrough so the bridge keeps working.

No secrets: only tool names + kwargs cross the subprocess boundary and the
runner never logs request/response bodies.
"""

from __future__ import annotations

from typing import Any

# ── Security guardrails (additive) ─────────────────────────────────────
try:
    from rugbrain.guardrails import redact_output as _redact_output_fn
    from rugbrain.guardrails import sanitize_query as _sanitize_query_fn

    redact_output = _redact_output_fn
    sanitize_query = _sanitize_query_fn
    _GUARDRAILS_OK = True
except ImportError:  # pragma: no cover - guardrails ships with the package
    _GUARDRAILS_OK = False

from rugbrain.mcp_bridge.augment import augment_code_query
from rugbrain.mcp_bridge.cli import _cli_main
from rugbrain.mcp_bridge.config import (
    _ASYNC_TOOLS,
    _REPO_ROOT,
    _RUNNER,
    _SCRIPTS_DIR,
    _SYNC_CODEBASE_TOOLS,
    _SYNC_TOOLS,
    MCP_DIR,
    MCP_PYTHON,
    MCP_TIMEOUT,
    BridgeUnavailableError,
)
from rugbrain.mcp_bridge.dispatch import (
    _call_direct,
    _call_mcp,
    _call_subprocess,
    _direct_import_available,
)
from rugbrain.mcp_bridge.fallbacks_kb import (
    _fallback_codebase,
    _fallback_resolve,
    _fallback_security,
)
from rugbrain.mcp_bridge.fallbacks_search import _fallback_search, _fallback_trace
from rugbrain.mcp_bridge.guards import _guard_query, _redact_result, _redact_text
from rugbrain.mcp_bridge.status import mcp_available, mcp_status
from rugbrain.mcp_bridge.sync_patterns import (
    _ecosystem_to_lang,
    _slugify,
    sync_security_patterns,
)
from rugbrain.mcp_bridge.tools_kb import get_security_context, query_codebase, resolve_library
from rugbrain.mcp_bridge.tools_search import (
    _normalize_code_hit,
    search_codebase,
    trace_codebase,
)

__all__ = [
    "MCP_DIR",
    "MCP_PYTHON",
    "MCP_TIMEOUT",
    "_ASYNC_TOOLS",
    "_GUARDRAILS_OK",
    "_REPO_ROOT",
    "_RUNNER",
    "_SCRIPTS_DIR",
    "_SYNC_CODEBASE_TOOLS",
    "_SYNC_TOOLS",
    "BridgeUnavailableError",
    "_call_direct",
    "_call_mcp",
    "_call_subprocess",
    "_cli_main",
    "_direct_import_available",
    "_ecosystem_to_lang",
    "_fallback_codebase",
    "_fallback_resolve",
    "_fallback_search",
    "_fallback_security",
    "_fallback_trace",
    "_guard_query",
    "_normalize_code_hit",
    "_redact_result",
    "_redact_text",
    "_slugify",
    "augment_code_query",
    "get_security_context",
    "mcp_available",
    "mcp_status",
    "query_codebase",
    "resolve_library",
    "search_codebase",
    "sync_security_patterns",
    "trace_codebase",
]


def __getattr__(name: str) -> Any:
    """Forward unknown attribute lookups to submodules (test-compat shim)."""
    import importlib

    for mod_name in (
        "config",
        "guards",
        "dispatch",
        "status",
        "tools_search",
        "tools_kb",
        "augment",
        "fallbacks_search",
        "fallbacks_kb",
        "sync_patterns",
        "cli",
    ):
        mod = importlib.import_module(f"rugbrain.mcp_bridge.{mod_name}")
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

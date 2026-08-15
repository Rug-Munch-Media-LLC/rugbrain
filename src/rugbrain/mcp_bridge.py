"""RugBrain ↔ RMI Codebase MCP bridge.

Lets the RAG answer code-related queries with deeper answers from the local
RMI Codebase MCP (FastMCP over stdio, running at
``/srv/work/repos/rmi-codebase-mcp``).

Bridge functions:
    search_codebase(query)            → MCP ``rmi_search()``
    trace_codebase(function)          → MCP ``rmi_trace()``
    get_security_context(query)       → MCP ``query_security_context()``
    sync_security_patterns(limit)     → pull MCP ChromaDB patterns into RAG
    augment_code_query(query)         → search + security context in one call

Execution strategy (in order):
  1. In-process direct import — only when ``fastmcp`` + ``chromadb`` are
     importable in the current interpreter.
  2. Subprocess runner — ``scripts/mcp_tool_runner.py`` executed with
     ``RMI_MCP_PYTHON`` (defaults to the x402 venv that ships the MCP server).
  3. RAG fallback — use RugBrain's own coding/security knowledge so the
     bridge degrades gracefully when the MCP is unreachable.

Environment:
    RMI_MCP_DIR      — codebase MCP server dir (default /srv/work/repos/rmi-codebase-mcp)
    RMI_MCP_PYTHON   — interpreter with fastmcp+chromadb (default the x402 venv)
    RMI_MCP_TIMEOUT  — subprocess timeout seconds (default 90)

No secrets: only tool names + kwargs cross the subprocess boundary and the
runner never logs request/response bodies.

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
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

# ── Security guardrails (additive) ─────────────────────────────────────
try:
    from rugbrain.guardrails import redact_output, sanitize_query

    _GUARDRAILS_OK = True
except ImportError:  # pragma: no cover - guardrails ships with the package
    _GUARDRAILS_OK = False


def _guard_query(query: str) -> tuple[str, dict[str, Any]]:
    """Sanitize *query* for prompt injection.

    Returns ``(safe_query, guard_meta)``. ``guard_meta`` carries
    ``flagged`` / ``reasons`` / ``sanitized`` / ``refused``; ``refused``
    is True when the query was flagged AND nothing usable remains after
    stripping (callers should fail closed and NOT forward). Never raises.
    """
    if not _GUARDRAILS_OK:
        return query, {"flagged": False, "reasons": [], "sanitized": False, "refused": False}
    try:
        sq = sanitize_query(query)
        safe = sq.query
        # Fail-closed: refuse when the flagged query strips to nothing OR a
        # trivial stub (<=3 chars) — forwarding a bare "and" would return
        # garbage while still having touched the MCP.
        refused = sq.flagged and len(safe.strip()) <= 3
        return safe, {
            "flagged": sq.flagged,
            "reasons": list(sq.reasons),
            "sanitized": safe != query,
            "refused": refused,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("guardrails sanitize failed: %s", str(exc)[:120])
        return query, {"flagged": False, "reasons": [], "sanitized": False, "refused": False}


def _redact_text(value: Any) -> Any:
    """Apply ``redact_output`` to a string (passthrough on failure)."""
    if isinstance(value, str) and _GUARDRAILS_OK:
        try:
            return redact_output(value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("guardrails redact failed: %s", str(exc)[:120])
    return value


def _redact_result(result: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Return *result* with the given string fields redacted (never mutates)."""
    if not result:
        return result
    out = dict(result)
    for key in keys:
        if key in out:
            out[key] = _redact_text(out[key])
    return out


# ── Config ──────────────────────────────────────────────────────────────
MCP_DIR = os.getenv("RMI_MCP_DIR", "/srv/work/repos/rmi-codebase-mcp")
MCP_PYTHON = os.getenv("RMI_MCP_PYTHON", "/srv/work/repos/rmi-mcp-x402/.venv/bin/python")
MCP_TIMEOUT = float(os.getenv("RMI_MCP_TIMEOUT", "90"))

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_RUNNER = _SCRIPTS_DIR / "mcp_tool_runner.py"

# MCP tools that are async in codebase_server.py.
_ASYNC_TOOLS = {"search", "trace", "refresh", "status"}
# MCP tools that are sync in server.py.
_SYNC_TOOLS = {"security", "patterns"}


class BridgeUnavailableError(RuntimeError):
    """Raised when the MCP server cannot be reached by any bridge path."""


# ── Low-level dispatch ──────────────────────────────────────────────────


def _direct_import_available() -> bool:
    """True when fastmcp + chromadb are importable in this interpreter."""
    try:
        import chromadb  # noqa: F401
        import fastmcp  # noqa: F401

        return True
    except ImportError:
        return False


async def _call_direct(tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """In-process bridge: import the MCP server modules and call the tool."""
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))

    if tool in _ASYNC_TOOLS:
        import codebase_server as cs

        fn = {
            "search": cs.rmi_search,
            "trace": cs.rmi_trace,
            "refresh": cs.rmi_refresh,
            "status": cs.rmi_status,
        }[tool]
        async with cs.lifespan(None):
            return cast(dict[str, Any], await fn(**kwargs))
    if tool == "security":
        import server as srv

        return cast(dict[str, Any], srv.query_security_context(**kwargs))
    if tool == "patterns":
        import server as srv

        return cast(dict[str, Any], srv.list_patterns(**kwargs))
    raise BridgeUnavailableError(f"unknown tool: {tool}")


async def _call_subprocess(tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Subprocess bridge: run scripts/mcp_tool_runner.py with the MCP python."""
    if not _RUNNER.exists():
        raise BridgeUnavailableError(f"runner not found: {_RUNNER}")
    if not os.path.exists(MCP_PYTHON):  # noqa: ASYNC240
        raise BridgeUnavailableError(f"MCP python not found: {MCP_PYTHON}")

    cmd = [MCP_PYTHON, str(_RUNNER), tool, json.dumps(kwargs)]
    env = dict(os.environ)
    env["RMI_MCP_DIR"] = MCP_DIR

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=MCP_TIMEOUT)
    except TimeoutError as exc:
        raise BridgeUnavailableError(f"MCP {tool} timed out after {MCP_TIMEOUT}s") from exc

    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise BridgeUnavailableError(f"MCP {tool} failed rc={proc.returncode}: {detail}")

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise BridgeUnavailableError(f"MCP {tool} returned invalid JSON") from exc

    if not payload.get("ok"):
        raise BridgeUnavailableError(f"MCP {tool} error: {payload.get('error', 'unknown')}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise BridgeUnavailableError(f"MCP {tool} returned non-dict result")
    return result


async def _call_mcp(tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Dispatch *tool* to the MCP server (direct import, then subprocess)."""
    if _direct_import_available():
        try:
            return await _call_direct(tool, kwargs)
        except BridgeUnavailableError:
            raise
        except Exception as exc:  # defensive — any in-process failure → subprocess
            logger.warning("mcp_bridge direct import failed tool=%s err=%s", tool, str(exc)[:160])
    return await _call_subprocess(tool, kwargs)


async def mcp_status() -> dict[str, Any]:
    """Return the MCP server's own status dashboard (rmi_status)."""
    try:
        return await _call_mcp("status", {})
    except BridgeUnavailableError as exc:
        return {"ok": False, "bridge": "unavailable", "error": str(exc)}


async def mcp_available() -> bool:
    """Cheap liveness probe — True when the MCP status tool responds."""
    try:
        status = await _call_mcp("status", {})
        return bool(status.get("ok") or status)
    except BridgeUnavailableError:
        return False


# ── Public bridge API ───────────────────────────────────────────────────


async def search_codebase(
    query: str,
    *,
    project: str | None = None,
    ecosystem: str | None = None,
    chain: str | None = None,
    code_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search the RMI codebase + security-pattern KB (MCP ``rmi_search``).

    Returns a normalized dict:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "search_codebase",
          "query", "results": [{kind, id, content, score, metadata}], "total",
          "raw": <MCP payload when bridge=mcp>,
        }

    Falls back to RugBrain's own coding knowledge when the MCP is unreachable.

    Guarded: the query is sanitized for prompt injection before dispatch
    (``guard`` metadata on the response; refusal when nothing remains).
    Result content is redacted for secret patterns before returning.
    """
    safe_query, guard = _guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "search_codebase",
            "query": query,
            "results": [],
            "total": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        raw = await _call_mcp(
            "search",
            {
                "query": safe_query,
                "project": project,
                "ecosystem": ecosystem,
                "chain": chain,
                "code_type": code_type,
                "limit": limit,
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("search_codebase MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _fallback_search(query, limit=limit)

    results: list[dict[str, Any]] = []
    for item in raw.get("code_results", []) or []:
        results.append(_normalize_code_hit(item, kind="code"))
    for item in raw.get("security_patterns", []) or []:
        results.append(_normalize_code_hit(item, kind="security_pattern"))

    results = [_redact_result(r, ("content", "document", "code_answer", "title")) for r in results]
    return {
        "ok": True,
        "bridge": "mcp",
        "tool": "search_codebase",
        "query": query,
        "results": results[:limit],
        "total": len(results),
        "raw": raw,
        "guard": guard,
    }


async def trace_codebase(
    function: str,
    *,
    project: str | None = None,
    direction: str = "both",
    depth: int = 3,
) -> dict[str, Any]:
    """Trace a function's call chain with security overlay (MCP ``rmi_trace``).

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "trace_codebase",
          "function", "trace", "security_flags", "backend",
        }

    Falls back to RugBrain's own coding knowledge when the MCP is unreachable.
    """
    try:
        raw = await _call_mcp(
            "trace",
            {
                "function_name": function,
                "project": project,
                "direction": direction,
                "depth": depth,
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("trace_codebase MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _fallback_trace(function, depth=depth)

    return {
        "ok": True,
        "bridge": "mcp",
        "tool": "trace_codebase",
        "function": function,
        "trace": raw.get("trace"),
        "security_flags": raw.get("security_flags", []),
        "backend": raw.get("trace_backend", "unknown"),
    }


async def get_security_context(
    query: str,
    *,
    top_k: int = 5,
    filter_severity: str | None = None,
    filter_ecosystem: str | None = None,
    filter_category: str | None = None,
    format: str = "compact",
) -> dict[str, Any]:
    """Query the Web3 security-pattern KB (MCP ``query_security_context``).

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "get_security_context",
          "query", "results": [{id, title, description, relevance_score, metadata, code_block?}],
          "count",
        }

    Falls back to RugBrain's own scam/coding knowledge when the MCP is unreachable.

    Guarded: the query is sanitized for prompt injection before dispatch
    (``guard`` metadata on the response; refusal when nothing remains).
    Result content is redacted for secret patterns before returning.
    """
    safe_query, guard = _guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "get_security_context",
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        raw = await _call_mcp(
            "security",
            {
                "query": safe_query,
                "top_k": top_k,
                "filter_severity": filter_severity,
                "filter_ecosystem": filter_ecosystem,
                "filter_category": filter_category,
                "format": format,
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("get_security_context MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _fallback_security(query, top_k=top_k)

    results = raw.get("results", []) or []
    results = [_redact_result(r, ("description", "code_block", "title", "content")) for r in results]
    return {
        "ok": bool(raw.get("success", True)),
        "bridge": "mcp",
        "tool": "get_security_context",
        "query": query,
        "results": results[:top_k],
        "count": len(results),
        "guard": guard,
    }


async def augment_code_query(query: str, *, limit: int = 8) -> dict[str, Any]:
    """One-call entry point for code-related RAG queries.

    Runs ``search_codebase`` + ``get_security_context`` and merges the
    results so the RAG can produce a code-first answer with security context
    without managing the two bridge calls itself. Falls back to RAG-only
    knowledge per sub-call when the MCP is down.

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback" | "mixed"),
          "query", "results", "security_patterns", "total", "raw": {...},
          "guard": {...},
        }

    Guarded: the query is sanitized once here; sub-calls refuse before
    dispatch when nothing usable remains. Aggregated ``guard`` metadata is
    attached to the merged response.
    """
    safe_query, guard = _guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "augment_code_query",
            "query": query,
            "results": [],
            "security_patterns": [],
            "total": 0,
            "raw": None,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    search = await search_codebase(safe_query, limit=limit)
    security = await get_security_context(safe_query, top_k=min(limit, 5))

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
    return {
        "ok": bool(search.get("ok")) or bool(security.get("ok")),
        "bridge": bridge,
        "tool": "augment_code_query",
        "query": query,
        "results": results,
        "security_patterns": security_results,
        "total": len(results) + len(security_results),
        "raw": {
            "search": search.get("raw") if search.get("bridge") == "mcp" else None,
            "security": security.get("results") if security.get("bridge") == "mcp" else None,
        },
        "guard": agg_guard,
    }


# ── RAG fallbacks ───────────────────────────────────────────────────────


async def _fallback_search(query: str, *, limit: int) -> dict[str, Any]:
    """Use RugBrain's own coding knowledge when the MCP is unavailable.

    Guarded: the query is sanitized before the RAG search and result
    content is redacted for secret patterns before returning.
    """
    safe_query, guard = _guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "search_codebase",
            "query": query,
            "results": [],
            "total": 0,
            "raw": None,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        from rugbrain.search_service import search_coding_knowledge  # lazy import

        res = await search_coding_knowledge(safe_query, limit=limit)
        results = []
        for r in res.get("results", []) or []:
            results.append(
                _redact_result(
                    {
                        "kind": "code" if r.get("has_code_snippet") else "knowledge",
                        "id": r.get("id"),
                        "content": r.get("content", ""),
                        "code_blocks": r.get("code_blocks", []),
                        "code_answer": r.get("code_answer", ""),
                        "score": r.get("similarity", 0.0),
                        "metadata": r.get("metadata", {}),
                        "source": r.get("source", ""),
                    },
                    ("content", "code_answer", "source"),
                )
            )
        return {
            "ok": True,
            "bridge": "rag_fallback",
            "tool": "search_codebase",
            "query": query,
            "results": results,
            "total": len(results),
            "raw": None,
            "guard": guard,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rag_fallback_search failed err=%s", str(exc)[:160])
        return {
            "ok": False,
            "bridge": "rag_fallback",
            "tool": "search_codebase",
            "query": query,
            "results": [],
            "total": 0,
            "raw": None,
            "guard": guard,
            "error": str(exc)[:200],
        }


async def _fallback_trace(function: str, *, depth: int) -> dict[str, Any]:
    """Return coding knowledge about *function* when the MCP is unavailable.

    Guarded: the function symbol is sanitized and result content redacted
    via the search fallback (defense in depth for the answer path).
    """
    fallback = await _fallback_search(function, limit=depth * 3)
    return {
        "ok": fallback.get("ok", False),
        "bridge": "rag_fallback",
        "tool": "trace_codebase",
        "function": function,
        "trace": None,
        "security_flags": [],
        "backend": "rag_fallback",
        "results": fallback.get("results", []),
        "guard": fallback.get("guard", {}),
    }


async def _fallback_security(query: str, *, top_k: int) -> dict[str, Any]:
    """Search RugBrain's scam/coding KB for security patterns.

    Guarded: the query is sanitized before the RAG search and result
    content is redacted for secret patterns before returning.
    """
    safe_query, guard = _guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "get_security_context",
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        from rugbrain.search_service import search_scam_knowledge  # lazy import

        res = await search_scam_knowledge(safe_query, limit=top_k)
        results = []
        for r in res.get("results", []) or []:
            results.append(
                _redact_result(
                    {
                        "id": r.get("id"),
                        "title": r.get("source", "pattern"),
                        "description": r.get("content", "")[:400],
                        "relevance_score": r.get("similarity", 0.0),
                        "metadata": r.get("metadata", {}),
                    },
                    ("title", "description"),
                )
            )
        return {
            "ok": True,
            "bridge": "rag_fallback",
            "tool": "get_security_context",
            "query": query,
            "results": results[:top_k],
            "count": len(results),
            "guard": guard,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rag_fallback_security failed err=%s", str(exc)[:160])
        return {
            "ok": False,
            "bridge": "rag_fallback",
            "tool": "get_security_context",
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": str(exc)[:200],
        }


# ── Helpers / sync ──────────────────────────────────────────────────────


def _normalize_code_hit(item: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Normalize an rmi_search hit into the bridge result shape."""
    return {
        "kind": kind,
        "id": item.get("id"),
        "content": item.get("document") or item.get("content") or "",
        "score": float(item.get("score") or item.get("similarity") or 0.0),
        "metadata": item.get("metadata") or {},
    }


async def sync_security_patterns(limit: int = 100, dry_run: bool = False) -> dict[str, Any]:
    """Pull MCP ChromaDB security patterns into the RAG ``coding_knowledge``.

    Lists patterns from the MCP (``list_patterns``), converts each into a
    coding-knowledge doc (title + description + code block), and ingests them
    into the ``coding_knowledge`` collection via the RAG's own store so the
    RAG can answer security questions even when the MCP is offline.

    Returns ingestion stats; ``dry_run=True`` only reports the doc count.
    """
    try:
        raw = await _call_mcp("patterns", {"limit": min(limit, 100)})
    except BridgeUnavailableError as exc:
        logger.info("sync_security_patterns MCP unavailable: %s", str(exc)[:120])
        return {"ok": False, "bridge": "unavailable", "error": str(exc), "ingested": 0}

    patterns = raw.get("patterns", []) or []
    docs: list[dict[str, Any]] = []
    for p in patterns:
        title = p.get("title", "Untitled")
        description = p.get("description", "")
        code = p.get("code_block", "") if "code_block" in p else ""
        content = f"{title}. {description}"
        if code:
            content += f"\n```\n{code}\n```"
        docs.append(
            {
                "title": title,
                "content": content,
                "ecosystem": _ecosystem_to_lang(p.get("ecosystem", "")),
                "category": p.get("category", "security_pattern"),
                "severity": p.get("severity", "medium"),
            }
        )

    if not docs:
        return {"ok": True, "bridge": "mcp", "ingested": 0, "patterns": 0, "dry_run": dry_run}

    if dry_run:
        return {
            "ok": True,
            "bridge": "mcp",
            "ingested": 0,
            "patterns": len(docs),
            "dry_run": True,
        }

    try:
        # Reuse the RAG's own store + the shared coding-doc builder.
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        import httpx
        from coding_ingest_common import build_coding_doc, ingest_coding_docs

        from rugbrain.qdrant_store import QdrantVectorStore

        coding_docs: list[dict[str, Any]] = []
        for d in docs:
            coding_docs.append(
                build_coding_doc(
                    source="mcp-security-patterns",
                    rel_key=f"mcp/{_slugify(d['title'])}",
                    content=d["content"],
                    ecosystem=d["ecosystem"],
                    language=d["ecosystem"],
                    knowledge_type="security_pattern",
                    topic=d["category"],
                    title=d["title"],
                    severity=d["severity"],
                )
            )
        async with httpx.AsyncClient(timeout=120.0) as client:
            store = QdrantVectorStore()
            try:
                stats = await ingest_coding_docs(
                    client,
                    store,
                    coding_docs,
                    source="mcp-security-patterns",
                    dry_run=False,
                )
            finally:
                await store.close()
        return {
            "ok": True,
            "bridge": "mcp",
            "ingested": int(stats.get("inserted", 0)),
            "patterns": len(docs),
            "chunks": int(stats.get("chunks", 0)),
            "failed": int(stats.get("failed", 0)),
            "dry_run": False,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sync_security_patterns ingest failed err=%s", str(exc)[:200])
        return {"ok": False, "bridge": "mcp", "error": str(exc)[:200], "ingested": 0}


def _ecosystem_to_lang(ecosystem: str) -> str:
    """Map MCP ecosystem tags to RAG language tags."""
    mapping = {
        "EVM": "solidity",
        "Solana": "rust",
        "Cosmos": "cosmwasm",
        "ZK": "circom",
        "AI": "python",
    }
    return mapping.get(ecosystem, "solidity")


def _slugify(text: str) -> str:
    """Slugify a pattern title for use as a stable rel_key component."""
    return "".join(c if c.isalnum() else "-" for c in (text or "").lower()).strip("-")[:80]


# ── CLI ─────────────────────────────────────────────────────────────────


async def _cli_main(argv: list[str]) -> int:
    """Minimal CLI: python -m rugbrain.mcp_bridge <tool> <json-or-query>."""
    if len(argv) < 2:
        print(
            "usage: python -m rugbrain.mcp_bridge "
            "{status|search|trace|security|sync} [query-or-json]"
        )
        return 2
    tool = argv[1]
    arg = argv[2] if len(argv) > 2 else None

    if tool == "status":
        result = await mcp_status()
    elif tool == "sync":
        limit = int(arg) if arg and arg.isdigit() else 100
        result = await sync_security_patterns(limit=limit)
    elif tool in {"search", "security"}:
        if not arg:
            print(f"{tool} requires a query")
            return 2
        result = await search_codebase(arg) if tool == "search" else await get_security_context(arg)
    elif tool == "trace":
        if not arg:
            print("trace requires a function name")
            return 2
        result = await trace_codebase(arg)
    else:
        print(f"unknown tool: {tool}")
        return 2

    print(json.dumps(result, default=str, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli_main(sys.argv)))

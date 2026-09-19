"""Low-level MCP dispatch: direct in-process import, then subprocess runner.

Execution strategy (in order):
  1. In-process direct import — only when ``fastmcp`` + ``chromadb`` are
     importable in the current interpreter.
  2. Subprocess runner — ``scripts/mcp_tool_runner.py`` executed with
     ``RMI_MCP_PYTHON`` (defaults to the x402 venv that ships the MCP server).

No secrets: only tool names + kwargs cross the subprocess boundary and the
runner never logs request/response bodies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, cast

from rugbrain.mcp_bridge.config import (
    _ASYNC_TOOLS,
    _RUNNER,
    _SYNC_CODEBASE_TOOLS,
    MCP_DIR,
    MCP_PYTHON,
    MCP_TIMEOUT,
    BridgeUnavailableError,
)

logger = logging.getLogger(__name__)


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
    if tool in _SYNC_CODEBASE_TOOLS:
        import codebase_server as cs

        fn = {
            "resolve_library": cs.resolve_library,
            "query_codebase": cs.query_codebase,
        }[tool]
        # Sync tools in codebase_server.py — no lifespan needed (Qdrant only).
        return cast(dict[str, Any], fn(**kwargs))
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

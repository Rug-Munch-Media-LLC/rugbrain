"""Serena layer: LSP symbol / reference lookup via MCP JSON-RPC.

Serena requires a session handshake before any symbol call:

``initialize`` (returns ``mcp-session-id``) -> ``notifications/initialized``
-> ``initial_instructions`` -> ``activate_project`` -> ``find_symbol``.

Skipping ``activate_project`` yields a plausible EMPTY answer, not an error,
so the adapter refuses rather than returning empty when activation fails.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from rugbrain.fleet_layers.envelope import layer_response
from rugbrain.fleet_layers.zoekt import _http_json

SOURCE = "serena"
BASE = "http://127.0.0.1:9092/mcp"
TIMEOUT = 8.0
FALLBACK = "zoekt_search for the identifier, then context-crypto search_text"


def _decode(body: bytes) -> dict[str, Any]:
    """Decode a JSON-RPC body that may arrive as SSE ``data:`` lines."""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if line.strip().startswith("data:"):
            try:
                out = json.loads(line.strip()[5:].strip()) or out
            except json.JSONDecodeError:
                continue
    return out


def _rpc(session: str | None, payload: dict[str, Any], timeout: float = TIMEOUT) -> tuple[int, dict[str, str], dict[str, Any]]:
    """One JSON-RPC round trip; carries the MCP session header when present."""
    headers = {"Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    status, hdrs, body = _http_json(BASE, payload=payload, headers=headers, timeout=timeout)
    return status, hdrs, _decode(body)


def _tool_text(resp: dict[str, Any]) -> str:
    """Flatten an MCP tools/call result into text."""
    try:
        parts = [c.get("text", "") for c in resp["result"]["content"] if c.get("type") == "text"]
    except (KeyError, TypeError):
        return ""
    return "\n".join(p for p in parts if p)


def serena_symbols(project: str, name_path_pattern: str, *, relative_path: str = "", depth: int = 0) -> dict[str, Any]:
    """Find a symbol by name path in an activated project.

    Returns the shared envelope; an empty hit sets ``data.empty`` explicitly.
    """
    if not project or not name_path_pattern:
        return layer_response(source=SOURCE, error_code="refused", detail="project and name_path_pattern are required", fallback=FALLBACK)
    try:
        status, hdrs, init = _rpc(
            None,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "rugbrain", "version": "1"},
                },
            },
        )
    except Exception as exc:
        code = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "unavailable"
        return layer_response(source=SOURCE, error_code=code, detail=f"{type(exc).__name__}: {exc}", fallback=FALLBACK)
    if status in (401, 403):
        return layer_response(source=SOURCE, error_code="unauthorized", detail=f"HTTP {status}", fallback=FALLBACK)
    session = hdrs.get("mcp-session-id") or hdrs.get("Mcp-Session-Id")
    if not session:
        return layer_response(source=SOURCE, error_code="unauthorized", detail="initialize returned no mcp-session-id", fallback=FALLBACK)
    if "result" not in init:
        return layer_response(source=SOURCE, error_code="bad_response", detail=str(init.get("error") or "no result in initialize"), fallback=FALLBACK)
    try:
        _rpc(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _rpc(session, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "initial_instructions", "arguments": {}}})
        act = _rpc(session, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "activate_project", "arguments": {"project": project}}})[2]
        if "error" in act:
            return layer_response(source=SOURCE, error_code="project_not_activated", detail=str(act["error"])[:300], fallback=FALLBACK)
        found = _rpc(
            session,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "find_symbol",
                    "arguments": {
                        "name_path_pattern": name_path_pattern,
                        "relative_path": relative_path,
                        "depth": depth,
                        "include_body": False,
                    },
                },
            },
        )[2]
    except Exception as exc:
        code = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "unavailable"
        return layer_response(source=SOURCE, error_code=code, detail=f"{type(exc).__name__}: {exc}", fallback=FALLBACK)
    if "error" in found:
        return layer_response(source=SOURCE, error_code="bad_response", detail=str(found["error"])[:300], fallback=FALLBACK)
    text = _tool_text(found)
    return layer_response(
        source=SOURCE,
        data={"project": project, "name_path_pattern": name_path_pattern, "symbols": text[:4000], "empty": text.strip() in ("", "[]")},
    )

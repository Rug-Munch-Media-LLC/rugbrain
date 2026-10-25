"""Minimal MCP streamable-HTTP client (session-aware) for the local bridges."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .envelope import iso, utcnow
from .fetch import ProviderError, classify

TIMEOUT_S = float(os.getenv("LIVE_HTTP_TIMEOUT", "8"))
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "rugbrain-live", "version": "1.0"},
    },
}


def parse_response(text: str) -> dict:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.startswith("{"):
            return json.loads(line)
    raise json.JSONDecodeError("no jsonrpc payload", text, 0)


def unwrap(result: Any) -> Any:
    """MCP tool results arrive as content parts; decode the first JSON text part."""
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        for part in result["content"]:
            text = part.get("text") if isinstance(part, dict) else None
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return result


async def mcp_call(
    url: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    provider: str,
) -> tuple[Any, str]:
    """Call one MCP tool. Retries once, handshakes a session only if demanded."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=body, headers=_HEADERS, timeout=TIMEOUT_S)
            resp.raise_for_status()
            payload = parse_response(resp.text)
            if "error" in payload and "session" in str(payload["error"]).lower():
                init = await client.post(url, json=_INIT, headers=_HEADERS, timeout=TIMEOUT_S)
                init.raise_for_status()
                session = init.headers.get("mcp-session-id", "")
                if not session:
                    raise ProviderError(provider, "mcp_error", "session handshake returned no id")
                resp = await client.post(
                    url, json=body, headers={**_HEADERS, "mcp-session-id": session}, timeout=TIMEOUT_S
                )
                resp.raise_for_status()
                payload = parse_response(resp.text)
    except Exception as exc:  # noqa: BLE001 - classified below
        raise classify(provider, exc) from exc
    if "error" in payload:
        raise ProviderError(provider, "mcp_error", str(payload["error"])[:180])
    return unwrap(payload.get("result")), iso(utcnow())

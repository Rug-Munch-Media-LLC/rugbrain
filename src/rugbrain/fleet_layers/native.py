"""RugBrain's own retrieval, as the LAST rung of a delegation.

When a better-owned app (zoekt for exact text, serena for symbols) refuses,
the caller falls back to the behaviour it had before delegation existed: this
adapter over RugBrain's own search API. The endpoint is configurable so a
deployment never hardcodes a port.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib.parse import urlencode

from rugbrain.fleet_layers.envelope import layer_response
from rugbrain.fleet_layers.zoekt import _http_json

SOURCE = "rugbrain"
BASE = os.getenv("RB_NATIVE_SEARCH_URL", "http://127.0.0.1:8000/api/v1/search")
TIMEOUT = float(os.getenv("RB_NATIVE_SEARCH_TIMEOUT", "6"))
FALLBACK = "no further layer: report the miss and name the layers already tried"


def native_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search RugBrain's own corpus; typed refusal when it is unreachable."""
    q = (query or "").strip()
    if not q:
        return layer_response(source=SOURCE, error_code="refused", detail="query is empty", fallback=FALLBACK)
    url = f"{BASE}?{urlencode({'q': q, 'limit': limit})}"
    try:
        status, _headers, body = _http_json(url, timeout=TIMEOUT)
    except Exception as exc:  # transport failure is a typed refusal, not a crash
        code = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "unavailable"
        return layer_response(source=SOURCE, error_code=code, detail=f"{type(exc).__name__}: {exc}", fallback=FALLBACK)
    if status in (401, 403):
        return layer_response(source=SOURCE, error_code="unauthorized", detail=f"HTTP {status}", fallback=FALLBACK)
    if status != 200:
        return layer_response(source=SOURCE, error_code="bad_response", detail=f"HTTP {status}", fallback=FALLBACK)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return layer_response(source=SOURCE, error_code="bad_response", detail="non-JSON body", fallback=FALLBACK)
    rows = parsed.get("results") if isinstance(parsed, dict) else None
    rows = rows if isinstance(rows, list) else (parsed.get("data") if isinstance(parsed, dict) else []) or []
    rows = rows if isinstance(rows, list) else []
    return layer_response(source=SOURCE, data={"query": q, "count": len(rows), "empty": not rows, "results": rows[:limit]})

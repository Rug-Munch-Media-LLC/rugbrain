"""Zoekt layer: exact string / regex search over committed corpora.

Probed 2026-10-25 on Talos: the MCP front at ``:9093`` answers only JSON-RPC
(``POST /api/search`` and ``POST /search`` are 404 there), while the index at
``:6070`` serves ``GET /search?q=...&format=json``. The adapter therefore talks
to the index surface and never guesses an MCP tool schema.

Zoekt sees COMMITTED code only; uncommitted work is rg-search's job.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

from rugbrain.fleet_layers.envelope import layer_response

SOURCE = "zoekt"
BASE = os.getenv("RB_ZOEKT_BASE", "http://127.0.0.1:6070")
TIMEOUT = float(os.getenv("RB_ZOEKT_TIMEOUT", "4"))
MAX_FILES = 50
FALLBACK = "rg-search codebase_search_text (sees uncommitted work) or context-crypto search_text"


def _http_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = TIMEOUT,
) -> tuple[int, dict[str, str], bytes]:
    """Minimal stdlib JSON request; returns (status, headers, raw body)."""
    data = None if payload is None else json.dumps(payload).encode()
    hdrs = {"Accept": "application/json"}
    hdrs.update(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


def _failure(exc: BaseException) -> dict[str, Any]:
    """Map a transport exception onto a typed refusal."""
    reason = getattr(exc, "reason", None)
    code = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)) else "unavailable"
    return layer_response(source=SOURCE, error_code=code, detail=f"{type(exc).__name__}: {exc}", fallback=FALLBACK)


def _files_from(body: dict[str, Any], *, files_only: bool) -> list[dict[str, Any]]:
    """Extract the bounded file list from a zoekt search payload."""
    rows: list[dict[str, Any]] = []
    for fm in (body.get("result") or {}).get("FileMatches") or []:
        row: dict[str, Any] = {"file": fm.get("FileName"), "repo": fm.get("Repository")}
        if not files_only:
            chunks = [c.get("Content", "") for c in (fm.get("ChunkMatches") or [])]
            row["matches"] = len(chunks)
            row["content"] = "\n".join(chunks)[:2000]
        rows.append(row)
        if len(rows) >= MAX_FILES:
            break
    return rows


def zoekt_search(query: str, files_only: bool = False) -> dict[str, Any]:
    """Exact string/regex search over the zoekt index.

    Returns the shared envelope; ``data.empty`` is explicit so a zero-hit run
    is never mistaken for a silent empty array.
    """
    q = (query or "").strip()
    if not q:
        return layer_response(source=SOURCE, error_code="refused", detail="query is empty", fallback=FALLBACK)
    url = f"{BASE}/search?{urlencode({'q': q, 'format': 'json'})}"
    try:
        status, _headers, body = _http_json(url)
    except Exception as exc:  # transport failure is a typed refusal, not a crash
        return _failure(exc)
    if status in (401, 403):
        return layer_response(source=SOURCE, error_code="unauthorized", detail=f"HTTP {status}", fallback=FALLBACK)
    if status != 200:
        looks_html = b"<html" in body[:200].lower()
        detail = f"HTTP {status}" + (" (SPA HTML, wrong surface)" if looks_html else "")
        return layer_response(source=SOURCE, error_code="bad_response", detail=detail, fallback=FALLBACK)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return layer_response(source=SOURCE, error_code="bad_response", detail="non-JSON body", fallback=FALLBACK)
    rows = _files_from(parsed, files_only=files_only)
    return layer_response(
        source=SOURCE,
        data={"query": q, "files_only": files_only, "count": len(rows), "empty": not rows, "files": rows},
    )

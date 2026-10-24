"""
rugbrain-mcp — FastMCP facade over RugBrain HTTP API (127.0.0.1:8012).

Routing rule (internal):
  code/protocol/exploit-pattern → context-crypto
  news/scam/wallet/incident/sentiment → rugbrain-mcp
  chain state → rugmunch-data

Does not deprecate context-crypto's query_rugbrain yet — plan:
  1. rugbrain-mcp ships with rugbrain_* tool prefix (this build)
  2. 2-week shadow period: agents use rugbrain_* tools, compare quality
  3. After validation: flip default routing in guide_lookup, mark query_rugbrain deprecated
  4. Remove query_rugbrain from context-crypto in a follow-up PR
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

# ── Config ────────────────────────────────────────────────────────────────
BASE = os.getenv("RUGBRAIN_URL", "http://127.0.0.1:8012").rstrip("/")
TIMEOUT = int(os.getenv("RUGBRAIN_TIMEOUT", "90"))


def _api_key() -> str:
    """Resolve the API key: env first, then the spooled secrets file.

    The MCP server is launched as a bare stdio command, so nothing upstream
    exports the key for us — refusing to start made the facade unreachable
    even though the credential exists on disk. Never logged, never printed.
    """
    key = os.getenv("RMI_RAG_API_KEY", "")
    if key:
        return key
    try:
        for line in Path("/srv/secrets/rugbrain.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("RMI_RAG_API_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    raise RuntimeError("RMI_RAG_API_KEY not found in env or /srv/secrets/rugbrain.env")


API_KEY = _api_key()

# ── Logging ───────────────────────────────────────────────────────────────
# Standard stdio-MCP requirement: JSON-RPC owns STDOUT, so any log line
# written there corrupts the protocol and the client fails the handshake.
# Route logs to STDERR explicitly.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("rugbrain-mcp")

# ── FastMCP server ────────────────────────────────────────────────────────
# ── Market-specialist surface plumbing (moved in 2026-10-12) ──────────────
RAG_API_HOST = os.getenv("RAG_API_HOST", "127.0.0.1")
RAG_API_PORT = int(os.getenv("RAG_API_PORT", "8012"))
BASE_URL = f"http://{RAG_API_HOST}:{RAG_API_PORT}"
# The RAGAuthMiddleware requires X-API-Key on every non-health path, so the
# async market-specialist surface must send it too — omitting it made
# rugbrain_market_brief / rugbrain_social_sentiment / rugbrain_scam_radar
# return 401 while the sync _call() tools worked.
HEADERS = {"Content-Type": "application/json", "X-API-Key": API_KEY}


class MultiSearchRequest(BaseModel):
    """Body for POST /api/v1/search/multi."""

    query: str = ""
    collections: list[str] = Field(default_factory=lambda: ["news_v2"])
    limit: int = 10
    limit_per_collection: int = 20
    rerank: bool = True


mcp = FastMCP("rugbrain-mcp")


# ── HTTP helper ───────────────────────────────────────────────────────────
def _call(
    path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call RugBrain API. Raises on failure — no silent empty returns."""
    url = f"{BASE}{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v not in (None, "")}
        if filtered:
            url = f"{url}?{urllib.parse.urlencode(filtered)}"

    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "X-API-Key": API_KEY}
    if data is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 — internal Qdrant/RugBrain URL
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 — internal Qdrant/RugBrain URL
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode(errors="replace")
        log.error("rugbrain_http_error", extra={"code": exc.code, "path": path, "detail": detail})
        raise RuntimeError(f"RugBrain HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        log.error("rugbrain_unreachable", extra={"path": path, "err": str(exc)[:200]})
        raise RuntimeError(f"RugBrain unreachable: {exc}") from exc

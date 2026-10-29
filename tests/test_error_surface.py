"""Error-surface smoke: key-gated endpoints must not 4xx/5xx with a valid key.

Skips unless ``RB_SMOKE_BASE`` and ``RMI_RAG_API_KEY`` are set, so CI stays
offline-safe; run against a live server with::

    RB_SMOKE_BASE=http://127.0.0.1:8012 RMI_RAG_API_KEY=... pytest tests/test_error_surface.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

BASE = os.environ.get("RB_SMOKE_BASE", "").rstrip("/")
KEY = os.environ.get("RMI_RAG_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not (BASE and KEY), reason="RB_SMOKE_BASE/RMI_RAG_API_KEY unset (offline)"
)

CASES = [
    ("GET", "/health", None),
    ("GET", "/api/v1/ta/stats", None),
    ("POST", "/api/v1/search/multi", {"query": "solana", "limit": 1}),
]


def _call(method: str, path: str, body: dict | None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - explicit http(s) base
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"X-API-Key": KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - http(s) only
            return r.status, r.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace")


@pytest.mark.parametrize("method,path,body", CASES, ids=[c[1] for c in CASES])
def test_endpoint_not_error(method: str, path: str, body: dict | None) -> None:
    status, text = _call(method, path, body)
    assert status < 400, f"{method} {path} -> {status}: {text}"

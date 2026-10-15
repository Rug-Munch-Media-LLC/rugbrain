"""The HTTP surface must not change when ``main.py`` is re-composed.

Splitting ``main.py`` moved router mounting into ``app/routes_registry.py``.
This test pins the resulting OpenAPI surface so a future re-composition that
drops, renames, or reorders a mount fails loudly instead of silently shrinking
the public API.
"""

from __future__ import annotations

from rugbrain import main

#: Every endpoint the app exposed before the 2026-09-28 app/ split.
BASELINE_ENDPOINTS: frozenset[str] = frozenset(
    {
        "GET /api/v1/alerts/live",
        "GET /api/v1/alerts/recent",
        "GET /api/v1/analyze/{address}",
        "GET /api/v1/analyze/{address}/full",
        "GET /api/v1/content/drafts",
        "GET /api/v1/content/trending",
        "GET /api/v1/email/accounts",
        "GET /api/v1/eval/run",
        "GET /api/v1/fear-index",
        "GET /api/v1/fear-index/history",
        "GET /api/v1/feed/all",
        "GET /api/v1/feed/sources",
        "GET /api/v1/feedback/stats",
        "GET /api/v1/health",
        "GET /api/v1/health/full",
        "GET /api/v1/intel/address-risk",
        "GET /api/v1/market/context/{address}",
        "GET /api/v1/market/mood",
        "GET /api/v1/news/rate",
        "GET /api/v1/news/top",
        "GET /api/v1/rag/chain/catalog",
        "GET /api/v1/rag/journalism/research",
        "GET /api/v1/rag/v2/scam-check",
        "GET /api/v1/rag/watches",
        "GET /api/v1/search",
        "GET /api/v1/search/crypto-code",
        "GET /api/v1/search/federated",
        "GET /api/v1/search/vector-landscape",
        "GET /api/v1/sentiment/market",
        "GET /api/v1/sentiment/token/{address}",
        "GET /api/v1/ta/signals",
        "GET /api/v1/ta/stats",
        "GET /api/v1/similar/{address}",
        "GET /health",
        "GET /metrics",
        "GET /ready",
        "POST /api/v1/alerts/webhook/register",
        "POST /api/v1/alerts/webhook/test",
        "POST /api/v1/alerts/webhook/unregister",
        "POST /api/v1/content/ack",
        "POST /api/v1/embed",
        "POST /api/v1/feedback/scam-confirmed",
        "POST /api/v1/feedback/scan-result",
        "POST /api/v1/ingest",
        "POST /api/v1/rag/chain/query",
        "POST /api/v1/rag/chunk-solidity",
        "POST /api/v1/rag/confidence",
        "POST /api/v1/rag/cross-chain",
        "POST /api/v1/rag/defi",
        "POST /api/v1/rag/digest",
        "POST /api/v1/rag/hyperliquid",
        "POST /api/v1/rag/image-analysis",
        "POST /api/v1/rag/investigate",
        "POST /api/v1/rag/logo-check",
        "POST /api/v1/rag/query",
        "POST /api/v1/rag/social",
        "POST /api/v1/rag/status/all",
        "POST /api/v1/rag/watch",
        "POST /api/v1/scanner/deployer-risk",
        "POST /api/v1/search/multi",
        "POST /api/v1/sentiment/analyze",
        "POST /embed",
    }
)


def _endpoints() -> set[str]:
    schema = main.app.openapi()
    return {
        f"{method.upper()} {path}"
        for path, ops in schema.get("paths", {}).items()
        for method in ops
        if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
    }


def test_http_surface_is_unchanged() -> None:
    """Every pre-split endpoint is still served — no silent API regression."""
    assert _endpoints() == set(BASELINE_ENDPOINTS)


def test_health_and_ready_are_public() -> None:
    """The auth middleware exempts these; they must never disappear."""
    endpoints = _endpoints()
    assert "GET /health" in endpoints
    assert "GET /ready" in endpoints


def test_embed_legacy_alias_survives() -> None:
    """``POST /embed`` is the compat alias older workers still call."""
    assert "POST /embed" in _endpoints()

"""
RAG optimization endpoints — shared helpers + router assembly.

Degodding phase 3 (2026-08-21): the 2035-line god module was split by
domain into ``rugbrain/routes/`` sub-routers, re-assembled below so URL
paths, tags, and auth behavior stay EXACT:

  - ``routes/core.py``      — health, embed
  - ``routes/analyze.py``   — /analyze/{address}, /analyze/{address}/full
  - ``routes/rag_tools.py`` — parquet SQL, confidence, chunking, identity,
                              multimodal, investigation, watches
  - ``routes/intel.py``     — journalism research, eval, email accounts,
                              news rating/top, fear index, market context
  - ``routes/search.py``    — /search, /search/multi + scam_check_router
  - ``routes/ingest.py``    — /ingest
  - ``routes/monitors.py``  — /rag/status/all + monitor snapshots

This module keeps the cross-route plumbing that tests monkeypatch:
``RATE_LIMITS``, ``_check_rate_limit``, ``_is_token_address`` and
``_analyze_address_findings`` (patched as ``rugbrain.endpoints.
_analyze_address_findings`` — the search/analyze handlers resolve it
through THIS module object at call time).
"""

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

RATE_LIMITS = {
    "free": 5,
    "pro": 100,
    # /api/v1/search is called by the backend scanner per token over a shared
    # egress IP, so the per-IP limit must be generous but still bound abuse
    # (each search costs an embedding + Qdrant query + optional rerank).
    "search": int(os.getenv("RAG_SEARCH_RATE_LIMIT", "3600")),
}


async def _check_rate_limit(identifier: str, tier: str, hour_key: str) -> tuple[bool, int]:
    """Check the per-identifier hourly rate limit.

    Fails OPEN when Redis is unavailable: rate limiting is a best-effort
    guard, not a hard dependency — the route should still serve results
    when the limiter backend is down.
    """
    from rugbrain.search_service import _get_redis

    try:
        rd = await _get_redis()
        limit = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
        key = f"rag:ratelimit:{tier}:{identifier}:{hour_key}"
        current = await rd.incr(key)
        if current == 1:
            now = datetime.now(UTC)
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            ttl = int((next_hour - now).total_seconds())
            await rd.expire(key, ttl)
        else:
            ttl = await rd.ttl(key)
            if ttl < 0:
                ttl = 3600
        if current > limit:
            return False, ttl
        return True, ttl
    except Exception as exc:
        logger.warning("rate_limit_unavailable: failing open — %s", exc)
        return True, 3600


router = APIRouter(prefix="/api/v1", tags=["rag-optimization"])


_TOKEN_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _is_token_address(q: str) -> bool:
    """True when *q* is a bare token/contract address (0x + 40 hex)."""
    return bool(_TOKEN_ADDRESS_RE.fullmatch((q or "").strip()))


async def _analyze_address_findings(address: str, chain: str, limit: int) -> dict[str, Any]:
    """Run the historical pattern-matching + cross-reference pipeline.

    Fail-open: any failure returns an empty finding block so the enclosing
    search/analyze endpoint still serves results.
    """
    try:
        from rugbrain.cross_reference import cross_reference_token
        from rugbrain.pattern_matching import match_token_patterns, score_scam_risk

        # Build a minimal token profile for matching (chain + address context).
        token_data = {"address": address, "chain": chain, "token_address": address}
        matching = await match_token_patterns(token_data)
        matched = matching.get("matched_patterns", [])
        signals = matching.get("risk_signals", [])
        verdict = score_scam_risk(matched, signals)

        cross = await cross_reference_token(
            address,
            chain=chain,
            limit=limit,
            matched_patterns=matched,
        )

        # Contract-security assessment (contract-knowledge expansion): fetch
        # verified source (Sourcify/Etherscan), run static heuristics, and
        # compare against the solidity_patterns / contract_security /
        # audit_findings knowledge collections. Fail-open: analyze returns a
        # benign dict when no source or knowledge backend is available.
        from rugbrain.contract_analysis import analyze_contract_security

        contract_security = await analyze_contract_security(address, chain=chain, limit=limit)

        # Fearful market → higher risk on new tokens (search/analyze feed).
        fear_value: int | None = None
        try:
            from rugbrain.fear_index import adjust_risk_for_market, current_fear_value

            fear_value = await current_fear_value()
            verdict = adjust_risk_for_market(verdict, fear_value)
        except Exception as e:
            logger.debug("fear_index_adjust_failed (non-critical): %s", e)

        return {
            "address": address,
            "chain": chain,
            "verdict": verdict,
            "fear_index": fear_value,
            "matched_patterns": matched[:limit],
            "risk_signals": signals[:25],
            "cross_reference": cross,
            "contract_security": contract_security,
        }
    except Exception as e:
        logger.warning("analyze_address_findings failed (non-critical): %s", e)
        return {
            "address": address,
            "chain": chain,
            "verdict": {"risk": "unknown", "confidence": 0.0, "top_pattern": ""},
            "fear_index": None,
            "matched_patterns": [],
            "risk_signals": [],
            "cross_reference": {
                "address": address,
                "chain": chain,
                "backend": "none",
                "similar_tokens": [],
                "narrative": "",
            },
            "contract_security": {
                "address": address,
                "chain": chain,
                "source_fetched": False,
                "backend": "none",
                "assessment": "unknown",
                "summary": "",
                "error": str(e)[:200],
            },
            "error": str(e)[:200],
        }


# ── Assemble the /api/v1 router from the domain sub-routers ─────────────
# Imported AFTER the shared helpers above: routes/analyze.py + routes/
# search.py bind this (partially initialized) module at import time and
# resolve the helpers through it at request time. Route paths are
# unchanged — the parent prefix /api/v1 applies exactly as before.
from rugbrain.routes import analyze as _analyze_routes  # noqa: E402
from rugbrain.routes import core as _core_routes  # noqa: E402
from rugbrain.routes import ingest as _ingest_routes  # noqa: E402
from rugbrain.routes import intel as _intel_routes  # noqa: E402
from rugbrain.routes import monitors as _monitors_routes  # noqa: E402
from rugbrain.routes import rag_tools as _rag_tools_routes  # noqa: E402
from rugbrain.routes import search as _search_routes  # noqa: E402

router.include_router(_core_routes.router)
router.include_router(_analyze_routes.router)
router.include_router(_rag_tools_routes.router)
router.include_router(_intel_routes.router)
router.include_router(_search_routes.router)
router.include_router(_ingest_routes.router)
router.include_router(_monitors_routes.router)

# Separate router mounted directly by main.py (prefix /api/v1/rag/v2).
# Back-compat re-exports: request models, constants, and handler functions
# that lived in this module before the split (e.g. tests import
# ``rugbrain.endpoints.search_threat_intel`` for signature inspection).
from rugbrain.routes.analyze import (  # noqa: E402,F401
    analyze_token_address,
    analyze_token_full,
)
from rugbrain.routes.core import EmbedRequest, embed_text, health  # noqa: E402,F401
from rugbrain.routes.ingest import (  # noqa: E402,F401
    IngestDocument,
    IngestRequest,
    ingest_documents,
)
from rugbrain.routes.intel import (  # noqa: E402,F401
    NEWS_TOP_MAX_SCROLL_PAGES,
    email_accounts,
    eval_run,
    fear_index_current,
    fear_index_history_endpoint,
    journalism_research,
    market_context_token_endpoint,
    market_mood_endpoint,
    news_rate_article,
    news_top_articles,
)
from rugbrain.routes.monitors import (  # noqa: E402,F401
    rag_defi,
    rag_digest,
    rag_hyperliquid,
    rag_social,
    rag_status_all,
)
from rugbrain.routes.rag_tools import (  # noqa: E402,F401
    rag_chain_catalog,
    rag_chain_query,
    rag_chunk_solidity,
    rag_confidence_score,
    rag_create_watch,
    rag_cross_chain,
    rag_image_analysis,
    rag_investigate_endpoint,
    rag_list_watches,
    rag_logo_check,
    rag_query_snapshots,
    scanner_deployer_risk,
)
from rugbrain.routes.search import (  # noqa: E402,F401
    MultiSearchRequest,
    scam_check,
    search_multi,
    search_threat_intel,
)
from rugbrain.routes.search import (  # noqa: E402
    scam_check_router as scam_check_router,
)

# Explicit re-exports for `from rugbrain.endpoints import ...` consumers
# (rugbrain/__init__.py, main.py) under no_implicit_reexport.
__all__ = ["router", "scam_check_router"]

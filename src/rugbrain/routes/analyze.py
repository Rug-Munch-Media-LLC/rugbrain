"""Token analysis routes: ``/analyze/{address}`` + ``/analyze/{address}/full``.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix here — the parent router in ``endpoints.py`` supplies
``/api/v1``.

``_analyze_address_findings`` stays defined in ``rugbrain.endpoints`` and
is resolved through the module object at CALL TIME (``_ep.
_analyze_address_findings``): tests monkeypatch
``rugbrain.endpoints._analyze_address_findings``, and a plain symbol
import bound at module-load time would silently bypass that patch.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from rugbrain import endpoints as _ep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rag-optimization"])


@router.get("/analyze/{address}")
async def analyze_token_address(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum", description="Chain of the token/contract"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Run the full historical scam-analysis pipeline on a token address.

    Combines the pattern-matching engine (bytecode + deployer behavior +
    token economics against known scam patterns) with historical
    cross-reference (similar tokens from lite-indexer LanceDB / Qdrant
    threat-intel) and returns a structured verdict::

        {
          "address", "chain",
          "verdict": {"risk": "high|medium|low|unknown", "confidence", "top_pattern"},
          "matched_patterns": [{"name", "confidence", "reference_incident", "technique"}],
          "risk_signals": ["..."],
          "cross_reference": {"backend", "similar_tokens", "narrative"},
        }

    Rate-limited per client IP (free tier = 5/hour).
    """
    now = datetime.now(UTC)
    hour_key = now.strftime("%Y%m%d%H")
    client_ip = request.client.host if request.client else "unknown"
    allowed, ttl = await _ep._check_rate_limit(client_ip, "free", hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": "free",
                "limit": _ep.RATE_LIMITS["free"],
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    if not _ep._is_token_address(address):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_address",
                "message": "address must be a 0x-prefixed 40-hex token/contract address",
            },
        )

    findings = await _ep._analyze_address_findings(address, chain, limit)
    return findings


@router.get("/analyze/{address}/full")
async def analyze_token_full(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum", description="Chain of the token/contract"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Comprehensive "everything we know about this token" analysis.

    Combines pattern matching, historical cross-reference (similar tokens),
    market context, social sentiment, code analysis (when contract source is
    verified) and known scam associations into a single report (Part 4)::

        curl "http://localhost:8012/api/v1/analyze/0xabc.../full?chain=ethereum"

    The scanner risk score is included when a prior scan of this address
    exists in the ``scanner_feedback`` collection. Every section is
    fail-open — an unavailable backend yields an empty block, never a 500.
    """
    if not _ep._is_token_address(address):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_address",
                "message": "address must be a 0x-prefixed 40-hex token/contract address",
            },
        )

    # ── Section 1: Pattern matching + cross-reference (historical) ──
    findings = await _ep._analyze_address_findings(address, chain, limit)

    # ── Section 2: Market context ──
    try:
        from rugbrain.market_context import build_token_market_context

        market_context = await build_token_market_context(address=address, chain=chain)
    except Exception as exc:
        logger.warning("analyze_full_market_failed: %s", exc)
        market_context = {
            "address": address,
            "chain": chain,
            "fear_index": 50.0,
            "market_sentiment": "neutral",
            "recent_exploits": [],
            "social_signals": {},
            "deployer_freshness": "unknown",
            "similar_tokens": [],
            "error": str(exc)[:200],
        }

    # ── Section 3: Scanner risk score (if a prior scan exists) ──
    scanner_score: dict[str, Any] = {"available": False}
    try:
        from rugbrain.qdrant_store import QdrantVectorStore, build_qdrant_filter
        from rugbrain.scanner_feedback import FEEDBACK_COLLECTION

        store = QdrantVectorStore()
        try:
            qdrant_filter = build_qdrant_filter({"metadata.token_address": address.lower()})
            results, _ = await store.scroll(
                FEEDBACK_COLLECTION,
                limit=1,
                filters=qdrant_filter,
            )
            if results:
                meta = results[0].get("metadata") or {}
                scanner_score = {
                    "available": True,
                    "risk_score": meta.get("risk_score", 0.0),
                    "risk_level": meta.get("risk_level", "unknown"),
                    "signals": meta.get("signals", []),
                    "scanner_version": meta.get("scanner_version", ""),
                    "ingested_at": meta.get("ingested_at", ""),
                }
        finally:
            await store.close()
    except Exception as exc:
        logger.debug("analyze_full_scanner_score_failed: %s", exc)

    # ── Section 4: Social sentiment ──
    try:
        from rugbrain.sentiment_engine import analyze_token_sentiment

        sentiment = await analyze_token_sentiment(address=address, chain=chain)
        social_sentiment = {
            "sentiment": sentiment.sentiment,
            "confidence": sentiment.confidence,
            "signals": sentiment.signals,
            "source_counts": sentiment.source_counts,
        }
    except Exception as exc:
        logger.debug("analyze_full_sentiment_failed: %s", exc)
        social_sentiment = {
            "sentiment": "unknown",
            "confidence": 0.0,
            "signals": [],
            "source_counts": {},
        }

    # ── Section 5: Code analysis (if contract verified) ──
    code_analysis = findings.get("contract_security", {})
    if not isinstance(code_analysis, dict):
        code_analysis = {}

    # ── Section 6: Known scam associations ──
    known_scams: dict[str, Any] = {"matched": False, "entries": []}
    try:
        from rugbrain.qdrant_store import QdrantVectorStore, build_search_filters

        store = QdrantVectorStore()
        try:
            scam_filter = build_search_filters(chain=chain, collection="threat_intel_v2")
            results, _ = await store.scroll(
                "threat_intel_v2",
                limit=10,
                filters=scam_filter,
            )
            entries = []
            for r in results or []:
                meta = r.get("metadata") or {}
                content = str(r.get("content", ""))[:300]
                if address.lower() in content.lower():
                    entries.append(
                        {
                            "severity": meta.get("severity", r.get("severity", "")),
                            "source": meta.get("source", r.get("source", "")),
                            "protocol": meta.get("protocol", ""),
                            "content": content,
                        }
                    )
            known_scams = {"matched": bool(entries), "entries": entries[:10]}
        finally:
            await store.close()
    except Exception as exc:
        logger.debug("analyze_full_scams_failed: %s", exc)

    return {
        "address": address,
        "chain": chain,
        "analyzed_at": datetime.now(UTC).isoformat(),
        "pattern_matching": {
            "verdict": findings.get("verdict"),
            "fear_index": findings.get("fear_index"),
            "matched_patterns": findings.get("matched_patterns", []),
            "risk_signals": findings.get("risk_signals", []),
        },
        "cross_reference": findings.get("cross_reference"),
        "market_context": market_context,
        "scanner_risk_score": scanner_score,
        "social_sentiment": social_sentiment,
        "code_analysis": code_analysis,
        "known_scam_associations": known_scams,
    }

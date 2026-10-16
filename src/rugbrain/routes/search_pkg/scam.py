"""``scam_check_router`` — the address scam check under ``/api/v1/rag/v2``.

Three-pillar search (scam reports, wallet profile, contract analysis) for a
token, contract, or wallet address.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from rugbrain.knowledge_boundary import sanitize_public_result
from rugbrain.routes.search_pkg._shared import enforce_rate_limit

logger = logging.getLogger(__name__)

scam_check_router: APIRouter = APIRouter(prefix="/api/v1/rag/v2", tags=["scam-check"])


@scam_check_router.get("/scam-check")
async def scam_check(
    request: Request,
    address: str,
    chain: str = "ethereum",
    limit: int = Query(default=10, ge=1, le=100),
    tier: str = Query(default="free", pattern="^(free|pro)$"),
) -> dict[str, Any]:
    await enforce_rate_limit(request, tier)

    from rugbrain.search_service import three_pillar_search

    query = f"scam check address {address} on {chain}"

    results_data = await three_pillar_search(
        query=query,
        # Search the collections where known-scam / scam-pattern data is
        # actually stored (known_scams + scam_patterns), plus the migrated
        # threat_intel_v2 collection and community reports. wallet_v2 is
        # included because per-address malicious labels (Etherscan
        # Fake_Phishing*) live there and nowhere else — without it a labelled
        # address returned "unknown".
        collections=[
            "known_scams",
            "scam_patterns",
            "threat_intel_v2",
            "rmi_community",
            "wallet_v2",
        ],
        limit=limit,
        min_similarity=0.4,
        entity_boost=2.0,
        use_mmr=False,
        use_kg=False,
        use_parent_child=False,
        use_reranker=False,
    )

    matches = []
    max_score = 0.0
    risk_signals = 0

    # Exact-address lookup FIRST. A wallet address is an identifier, and a
    # semantic search for "scam check address 0x…" does not retrieve the
    # address's own label row — so a labelled malicious address returned
    # "unknown". The label corpora are keyed on metadata.address; match it
    # exactly, exactly as wallet_profile does.
    from rugbrain.wallet_profile import _exact_hits

    for coll, payload in await _exact_hits(address):
        sev = str(payload.get("severity", "") or "")
        matches.append(
            sanitize_public_result(
                {
                    "id": str(payload.get("doc_id", "") or ""),
                    "content": (payload.get("content", "") or "")[:300],
                    "similarity": 1.0,
                    "severity": sev,
                    "collection": coll,
                    "source": str(payload.get("source", "") or ""),
                }
            )
        )
        max_score = max(max_score, 1.0)
        if sev in ("high", "critical"):
            risk_signals += 1

    for r in results_data.get("results", []):
        sim = r.get("similarity", 0)
        sev = r.get("severity", "")
        matches.append(
            sanitize_public_result(
                {
                    "id": r.get("id", ""),
                    "content": (r.get("content", "") or "")[:300],
                    "similarity": round(sim, 4),
                    "severity": sev,
                    "collection": r.get("metadata", {}).get("collection", r.get("collection", "")),
                    "source": r.get("source", ""),
                }
            )
        )
        max_score = max(max_score, sim)
        if sev in ("high", "critical"):
            risk_signals += 1

    if not matches:
        scam_risk = "unknown"
        confidence = 0
    elif max_score > 0.8 or risk_signals >= 2:
        scam_risk = "high"
        confidence = min(100, int(max_score * 100))
    elif max_score > 0.6 or risk_signals >= 1:
        scam_risk = "medium"
        confidence = min(85, int(max_score * 100))
    elif max_score > 0.4:
        scam_risk = "low"
        confidence = min(60, int(max_score * 100))
    else:
        scam_risk = "unknown"
        confidence = min(40, int(max_score * 100))

    return {
        "address": address,
        "chain": chain,
        "scam_risk": scam_risk,
        "matches": matches,
        "confidence": confidence,
        "total_matches": len(matches),
    }

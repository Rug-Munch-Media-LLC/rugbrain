"""Cross-chain identity resolution for the rmi-rag ``app`` layer.

Self-contained port of rmi-backend ``app.cross_chain`` for the rmi-rag
standalone container. Uses Qdrant payload metadata (wallet labels,
threat-intel records) plus optional funding/label hints to resolve
whether an address is known on other chains.

Synchronous by design: the ``/rag/cross-chain`` endpoint calls this
directly (non-awaited).
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333").rstrip("/")
SEARCH_COLLECTIONS = os.getenv(
    "RAG_CROSS_CHAIN_COLLECTIONS", "rmi_agent_memory,threat_intel,wallet"
)


def normalize_chain(chain: str) -> str:
    """Normalize chain names to lowercase with common aliases."""
    c = (chain or "").strip().lower()
    aliases = {
        "eth": "ethereum",
        "1": "ethereum",
        "bsc": "bnb",
        "binance": "bnb",
        "matic": "polygon",
        "137": "polygon",
        "avax": "avalanche",
        "arb": "arbitrum",
        "op": "optimism",
        "sol": "solana",
        "base": "base",
    }
    return aliases.get(c, c)


def detect_chain(address: str) -> str | None:
    """Detect blockchain from address format."""
    if re.match(r"^0x[a-fA-F0-9]{40}$", address):
        return "ethereum"
    if re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        return "solana"
    if re.match(r"^T[a-zA-Z0-9]{33}$", address):
        return "tron"
    return None


def analyze_funding_chain(
    address: str, funding_sources: list[dict[str, Any]], chain: str
) -> dict[str, Any]:
    """Summarize funding sources across chains."""
    chains = set()
    mixer_funded = False
    cross_chain_hops = 0
    total_amount = 0.0

    for src in funding_sources or []:
        src_chain = normalize_chain(src.get("chain", chain))
        chains.add(src_chain)
        if src_chain != normalize_chain(chain):
            cross_chain_hops += 1
        amount = src.get("amount") or 0
        if isinstance(amount, (int, float)):
            total_amount += float(amount)
        label = str(src.get("label", "")).lower()
        if "mixer" in label or "tornado" in label or "coinjoin" in label:
            mixer_funded = True

    return {
        "chains_involved": sorted(chains),
        "mixer_funded": mixer_funded,
        "cross_chain_hops": cross_chain_hops,
        "total_funding_usd": round(total_amount, 2),
        "source_count": len(funding_sources or []),
    }


def resolve_cross_chain_identity(
    address: str,
    chain: str,
    behavioral_fingerprint: Any = None,
    funding_sources: list[dict[str, Any]] | None = None,
    label_hints: list[str] | None = None,
    similar_addresses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Resolve cross-chain identity for a given address.

    Uses Qdrant payload metadata (wallet labels, threat-intel records,
    entity profiles) plus optional funding/label hints to find the same
    entity on other chains.

    Returns the same contract shape as rmi-backend ``app.cross_chain``:
    ``{address, chain, resolved, cross_chain_matches, total_matches,
    max_confidence, evidence, funding_analysis, summary}``.
    """
    chain = normalize_chain(chain)
    matches: list[dict[str, Any]] = []
    evidence: list[str] = []
    address_l = (address or "").lower()

    # 1. Behavioral fingerprint matching against similar_addresses
    if behavioral_fingerprint is not None and similar_addresses:
        for match in similar_addresses:
            m_addr = match.get("address", "")
            m_chain = normalize_chain(match.get("chain", ""))
            if m_chain == chain or not m_addr:
                continue
            sim = float(match.get("similarity") or match.get("confidence") or 0)
            if sim > 0.7:
                matches.append(
                    {
                        "address": m_addr,
                        "chain": m_chain,
                        "confidence": round(sim * 100),
                        "method": "behavioral_fingerprint",
                        "evidence": f"Behavioral fingerprint similarity: {sim:.2f}",
                    }
                )
                evidence.append(f"Behavioral fingerprint match on {m_chain}")

    # 2. Qdrant payload metadata lookup (wallet labels / entity profiles)
    try:
        with httpx.Client(base_url=QDRANT_URL, timeout=15.0) as client:
            for coll in [c.strip() for c in SEARCH_COLLECTIONS.split(",") if c.strip()]:
                r = client.post(
                    f"/collections/{coll}/points/scroll",
                    json={"limit": 100, "with_payload": True},
                )
                if r.status_code != 200:
                    continue
                for point in r.json().get("result", {}).get("points", []):
                    payload = point.get("payload", {})
                    meta = payload.get("metadata", {})
                    hit_addr = str(
                        meta.get("wallet_address")
                        or meta.get("token_address")
                        or meta.get("address")
                        or ""
                    )
                    if hit_addr.lower() != address_l:
                        continue
                    hit_chain = normalize_chain(meta.get("chain", chain))
                    labels = meta.get("labels") or meta.get("entities") or []
                    if isinstance(labels, str):
                        labels = [labels]
                    if hit_chain != chain and hit_addr:
                        matches.append(
                            {
                                "address": hit_addr,
                                "chain": hit_chain,
                                "confidence": 85,
                                "method": "qdrant_label",
                                "evidence": f"Label match in {coll}: {', '.join(labels[:3])}",
                            }
                        )
                        evidence.append(f"Known on {hit_chain} (collection {coll})")
                    elif labels and hit_addr:
                        evidence.append(f"Labels on {chain}: {', '.join(labels[:3])}")
    except Exception as exc:
        logger.debug("cross_chain_qdrant_lookup_failed: %s", exc)

    # 3. Funding source graph analysis
    if funding_sources:
        funding_analysis = analyze_funding_chain(address, funding_sources, chain)
        evidence.append(f"Funding touches {len(funding_analysis['chains_involved'])} chains")
        if funding_analysis["mixer_funded"]:
            evidence.append("Mixer-funded - possible laundering")
        if funding_analysis["cross_chain_hops"] > 0:
            evidence.append(
                f"{funding_analysis['cross_chain_hops']} cross-chain funding hops detected"
            )
    else:
        funding_analysis = analyze_funding_chain(address, funding_sources or [], chain)

    # 4. Label-based hints
    if label_hints:
        for hint in label_hints[:3]:
            evidence.append(f"Label match: {hint}")

    # ── Aggregate ──
    # Deduplicate matches by (address, chain)
    seen = set()
    unique_matches = []
    for m in matches:
        key = (m["address"], m["chain"])
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)

    resolved = bool(unique_matches)
    confidence = max((m["confidence"] for m in unique_matches), default=0)

    return {
        "address": address,
        "chain": chain,
        "resolved": resolved,
        "cross_chain_matches": unique_matches,
        "total_matches": len(unique_matches),
        "max_confidence": confidence,
        "evidence": evidence,
        "funding_analysis": funding_analysis,
        "summary": (
            f"Found {len(unique_matches)} cross-chain matches for {address[:10]}... on {chain}"
            if unique_matches
            else f"No cross-chain identity matches found for {address[:10]}... on {chain}"
        ),
    }

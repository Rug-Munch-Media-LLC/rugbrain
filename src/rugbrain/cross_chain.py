#!/usr/bin/env python3
"""
CROSS-CHAIN DEPLOYER FINGERPRINT DETECTION
===========================================
Detects when the same scammer deploys across multiple chains
using behavioral fingerprint matching + Qdrant vector search.

Marries bundle_cluster wallet clustering with cross-chain identity resolution.
"""

import hashlib
import logging
from typing import Any

import numpy as np

from rugbrain.bundle_cluster import embed_cluster_profile
from rugbrain.qdrant_store import get_qdrant_store

logger = logging.getLogger(__name__)


def _embed_deployer_fingerprint(wallet_profile: dict[str, Any]) -> list[float]:
    """
    Build a 128-dim deployer fingerprint vector from wallet behavior data.
    Captures: funding patterns, deployment cadence, code reuse, chain spread, risk signals.
    """
    vec = np.zeros(128, dtype=np.float32)
    metadata = wallet_profile.get("metadata", wallet_profile)

    # ── Funding signals (dims 0-15) ──
    vec[0] = min(float(metadata.get("mixer_funded_score", 0)), 1.0)
    vec[1] = min(float(metadata.get("tornado_interactions", 0)) / 10.0, 1.0)
    vec[2] = 1.0 if metadata.get("common_funder") else 0.0
    vec[3] = min(float(metadata.get("deployment_count", 1)) / 100.0, 1.0)
    vec[4] = min(float(metadata.get("creation_age_days", 0)) / 365.0, 1.0)

    # ── Deployment pattern (dims 10-25) ──
    vec[10] = 1.0 if metadata.get("rapid_deploy") or metadata.get("same_block_deploy") else 0.0
    vec[11] = min(float(metadata.get("avg_time_between_deploys_hours", 24)) / 24.0, 1.0)
    vec[12] = 1.0 if metadata.get("source_verified") else 0.0
    vec[13] = min(float(metadata.get("code_similarity_avg", 0)), 1.0)
    vec[14] = 1.0 if metadata.get("identical_bytecode") else 0.0

    # ── Risk labels (dims 20-35) ──
    labels = metadata.get("labels", []) or metadata.get("risk_tags", [])
    risk_tags = [
        "scam",
        "rug",
        "honeypot",
        "phishing",
        "drainer",
        "mixer",
        "sanctioned",
        "exploiter",
        "theft",
        "ransomware",
        "wash_trade",
        "insider",
        "sybil",
        "bot",
        "impersonator",
    ]
    for i, tag in enumerate(risk_tags):
        if tag in labels or tag in str(metadata.get("classification", "")):
            vec[20 + i] = 1.0

    # ── Current chain encoding (dims 36-51) ──
    chain = metadata.get("chain", "ethereum").lower()
    chain_list = [
        "ethereum",
        "solana",
        "base",
        "bsc",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
        "fantom",
        "tron",
        "sui",
        "aptos",
        "near",
        "blast",
        "linea",
        "zksync",
    ]
    for i, chain_name in enumerate(chain_list):
        if chain_name in chain:
            vec[36 + i] = 1.0

    # ── Funding address hash (dims 52-67) ──
    funder = str(metadata.get("funder_address", metadata.get("common_funder", "")))
    if funder:
        fh = hashlib.sha256(funder.encode()).digest()
        for i in range(16):
            vec[52 + i] = fh[i] / 255.0

    # ── Contract feature hash (dims 68-95) ──
    contract_features = metadata.get("contract_features", metadata.get("code_signature", ""))
    cf_str = str(contract_features)
    if cf_str:
        ch = hashlib.sha256(cf_str.encode()).digest()
        for i in range(28):
            vec[68 + i] = ch[i] / 255.0

    # ── Behavioral compound score (dims 96-127) ──
    behavior_profile = metadata.get("behavior_profile", metadata.get("cluster_profile", {}))
    if isinstance(behavior_profile, dict):
        vec[96] = min(float(behavior_profile.get("scam_probability", 0)), 1.0)
        vec[97] = min(float(behavior_profile.get("rug_probability", 0)), 1.0)
        vec[98] = min(float(behavior_profile.get("volume_usd", 0)) / 1_000_000.0, 1.0)
        vec[99] = min(float(behavior_profile.get("txn_count", 0)) / 1000.0, 1.0)
        vec[100] = min(float(behavior_profile.get("unique_counterparties", 0)) / 500.0, 1.0)

    full_hash = hashlib.sha256(str(sorted(wallet_profile.items())).encode()).digest()
    for i in range(27):
        vec[101 + i] = full_hash[i] / 255.0

    return vec.tolist()


async def _query_wallet_profile(
    address: str,
    chain: str = "ethereum",
) -> list[dict[str, Any]]:
    """Search Qdrant wallet_profiles for an exact address match."""
    store = await get_qdrant_store()

    dummy_vec = [0.0] * 128
    results = await store.search(
        dummy_vec,
        collection="wallet_profiles",
        limit=5,
        min_similarity=0.0,
        filters={"address": address},
    )
    return [
        r for r in results if r.get("metadata", {}).get("address", "").lower() == address.lower()
    ]


def _assess_cross_chain_risk(
    matches: list[dict[str, Any]],
    source_chain: str,
    profile_found: bool,
) -> dict[str, Any]:
    """Determine risk level from cross-chain match results."""
    if not profile_found:
        return {
            "is_cross_chain": False,
            "chains_found": [],
            "related_addresses": [],
            "risk": "low",
        }

    chains = set()
    addresses = []
    max_similarity = 0.0
    risk_signals = 0

    for m in matches:
        meta = m.get("metadata", {})
        match_chain = meta.get("chain", source_chain)
        match_addr = meta.get("address", meta.get("matched_address", ""))
        sim = m.get("similarity", 0)

        if match_chain != source_chain:
            chains.add(match_chain)
            addresses.append(
                {
                    "address": match_addr,
                    "chain": match_chain,
                    "similarity": sim,
                    "source": m.get("source", ""),
                    "severity": m.get("severity", "medium"),
                }
            )

        max_similarity = max(max_similarity, sim)
        if m.get("severity") in ("high", "critical"):
            risk_signals += 1

    chains_list = sorted(chains)
    is_cross_chain = len(chains_list) > 0

    if not is_cross_chain:
        return {
            "is_cross_chain": False,
            "chains_found": [],
            "related_addresses": [],
            "risk": "low",
        }

    if risk_signals >= 2 or max_similarity > 0.85:
        risk = "high"
    elif max_similarity > 0.7 or risk_signals >= 1:
        risk = "medium"
    else:
        risk = "low"

    return {
        "is_cross_chain": True,
        "chains_found": chains_list,
        "related_addresses": addresses,
        "risk": risk,
        "total_matches": len(addresses),
        "max_similarity": round(max_similarity, 4),
    }


async def check_cross_chain(
    address: str,
    chain: str = "ethereum",
) -> dict[str, Any]:
    """
    Detect if a deployer address operates across multiple chains.

    1. Queries wallet_profiles in Qdrant for the address.
    2. Builds a deployer behavioral fingerprint.
    3. Searches for matching fingerprints across other chains.
    4. Returns structured cross-chain assessment.
    """
    if not address:
        return {
            "is_cross_chain": False,
            "chains_found": [],
            "related_addresses": [],
            "risk": "low",
            "error": "address is required",
        }

    profile_results = await _query_wallet_profile(address, chain)
    profile_found = len(profile_results) > 0

    if not profile_found:
        logger.info("No wallet profile found for %s on %s", address, chain)
        return {
            "is_cross_chain": False,
            "chains_found": [],
            "related_addresses": [],
            "risk": "low",
        }

    profile = profile_results[0]
    fingerprint = _embed_deployer_fingerprint(profile)

    store = await get_qdrant_store()
    matches = await store.search(
        fingerprint,
        collection="wallet_profiles",
        limit=20,
        min_similarity=0.6,
    )

    return _assess_cross_chain_risk(matches, chain, profile_found)


async def fingerprint_wallet(
    address: str,
    profile_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Extract and store a deployer fingerprint for a wallet.
    Uses existing bundle_cluster patterns for behavioral embedding.
    """
    store = await get_qdrant_store()

    if profile_data is None:
        results = await _query_wallet_profile(address)
        if not results:
            return {"error": f"No wallet profile found for {address}"}
        profile_data = results[0]

    fingerprint = _embed_deployer_fingerprint(profile_data)
    metadata = profile_data.get("metadata", profile_data)

    cluster_data = {
        "size": 1,
        "wallet_count": 1,
        "edge_density": 0.0,
        "scam_probability": metadata.get("scam_probability", metadata.get("scam_score", 0)),
        "rug_probability": metadata.get("rug_probability", 0),
        "active_chains": [metadata.get("chain", "ethereum")],
        "behavior_signals": metadata.get("labels", []),
        "total_transactions": metadata.get("deployment_count", 0),
        "total_volume_usd": 0,
    }
    cluster_vec = embed_cluster_profile(cluster_data)

    fingerprint_id = hashlib.sha256(f"cross_chain_fingerprint:{address}".encode()).hexdigest()[:16]

    await store.insert(
        doc_id=fingerprint_id,
        collection="wallet_profiles",
        embedding=cluster_vec,
        content=f"Cross-chain deployer fingerprint for {address}",
        metadata={
            "address": address,
            "chain": metadata.get("chain", "ethereum"),
            "fingerprint": fingerprint,
            "scam_probability": metadata.get("scam_probability", 0),
            "deployment_count": metadata.get("deployment_count", 0),
            "labels": metadata.get("labels", []),
            "fingerprint_type": "cross_chain_deployer",
        },
        source="cross_chain_fingerprint",
        severity="high" if metadata.get("scam_probability", 0) > 0.5 else "medium",
        chain=metadata.get("chain", "ethereum"),
    )

    logger.info("Stored cross-chain fingerprint for %s", address)
    return {
        "fingerprint_id": fingerprint_id,
        "address": address,
        "fingerprint_dims": len(fingerprint),
    }

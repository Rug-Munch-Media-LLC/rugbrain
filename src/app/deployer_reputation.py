"""Deployer Reputation — behavioral fingerprint matching.

Self-contained port of rmi-backend ``app.domains.scanner.deployer_reputation``
for the rmi-rag standalone container. Analyzes deployer behavior patterns
to produce predictive risk scores.

Signals analyzed:
  - First deployment? (new deployers are higher risk)
  - Funded by mixer/tornado? (laundering attempt)
  - Funded by CEX hot wallet? (legitimate)
  - Source code verified? (legitimacy signal)
  - Matches known scammer behavioral fingerprint?
  - Deployment frequency (batch deployer?)
  - Contract bytecode similarity to known scams

Returns a 0-100 risk score with human-readable explanation.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Risk signal weights ──────────────────────────────────────────
SIGNALS = {
    "first_deployment": {
        "weight": 0.15,
        "description": "First-time deployer (no prior contracts)",
        "risk_level": "medium",
    },
    "mixer_funded": {
        "weight": 0.25,
        "description": "Funded via Tornado Cash or mixer",
        "risk_level": "critical",
    },
    "cex_funded": {
        "weight": -0.15,  # negative = reduces risk
        "description": "Funded via centralized exchange (likely KYC'd)",
        "risk_level": "low",
    },
    "unverified_source": {
        "weight": 0.10,
        "description": "Contract source code not verified",
        "risk_level": "medium",
    },
    "batch_deployer": {
        "weight": 0.15,
        "description": "Deployer has >10 contracts (potential factory/scam ring)",
        "risk_level": "high",
    },
    "known_scammer_match": {
        "weight": 0.30,
        "description": "Behavioral fingerprint matches known scammer",
        "risk_level": "critical",
    },
    "code_similar_to_scam": {
        "weight": 0.20,
        "description": "Contract bytecode similar to known scam patterns",
        "risk_level": "high",
    },
    "funded_by_scammer": {
        "weight": 0.35,
        "description": "Deployer funded by known scam address",
        "risk_level": "critical",
    },
    "cloned_contract": {
        "weight": 0.12,
        "description": "Exact bytecode match to another deployed contract (cloned token)",
        "risk_level": "medium",
    },
    "high_deployment_velocity": {
        "weight": 0.15,
        "description": "Multiple deployments in short time window",
        "risk_level": "high",
    },
}

# ── Known scammer behavioral fingerprints ────────────────────────
# Each is a vector of [deployment_count, avg_interval_hours, mixer_funded,
# verified_pct, code_uniqueness, funding_chain_length]
KNOWN_PATTERNS = {
    "rug_pull_factory": {
        "vector": [25, 2, 1, 0, 0.3, 1],
        "description": "Mass token deployer - dozens of unverified tokens, quick exits",
        "risk_modifier": 0.85,
    },
    "honeypot_operator": {
        "vector": [8, 48, 0, 0, 0.5, 2],
        "description": "Periodic deployer of honeypot tokens with similar bytecode",
        "risk_modifier": 0.80,
    },
    "phishing_ring": {
        "vector": [50, 1, 1, 0, 0.1, 1],
        "description": "High-volume airdrop phishing - hundreds of identical tokens",
        "risk_modifier": 0.95,
    },
    "impersonation_scammer": {
        "vector": [3, 720, 0, 1, 0.9, 1],
        "description": "Rare deployer, verified source, clones legitimate token code",
        "risk_modifier": 0.70,
    },
    "legitimate_dev": {
        "vector": [1, 8760, 0, 1, 1.0, 3],
        "description": "Single verified deployment from established developer",
        "risk_modifier": 0.05,
    },
}


def build_behavioral_fingerprint(
    address: str,
    deployment_count: int = 0,
    avg_interval_hours: float = 0,
    mixer_funded: bool = False,
    cex_funded: bool = False,
    source_verified: bool = False,
    code_uniqueness: float = 1.0,
    funding_chain_length: int = 0,
    rapid_deploy: bool = False,
    cloned_contract: bool = False,
    funded_by_scammer: bool = False,
) -> np.ndarray:
    """
    Build a behavioral fingerprint vector from deployer on-chain data.

    Vector dimensions:
      [0] deployment_count (normalized 0-50)
      [1] avg_interval_hours (normalized 0-168)
      [2] mixer_funded (0/1)
      [3] source_verified (0/1)
      [4] code_uniqueness (0-1)
      [5] funding_chain_length (normalized 0-10)
    """
    vec = np.array(
        [
            min(deployment_count, 50) / 50.0,
            min(avg_interval_hours, 168) / 168.0,
            1.0 if mixer_funded else 0.0,
            1.0 if source_verified else 0.0,
            code_uniqueness,
            min(funding_chain_length, 10) / 10.0,
        ],
        dtype=np.float32,
    )

    return vec


def score_deployer_risk(
    address: str,
    deployment_count: int = 0,
    avg_interval_hours: float = 0,
    mixer_funded: bool = False,
    cex_funded: bool = False,
    source_verified: bool = False,
    code_similarity: float = 0.0,
    code_uniqueness: float = 1.0,
    funding_chain_length: int = 0,
    rapid_deploy: bool = False,
    cloned_contract: bool = False,
    funded_by_scammer: bool = False,
) -> dict[str, Any]:
    """
    Score a deployer's pre-crime risk based on behavioral fingerprint.

    Parameters
    ----------
    address: Deployer wallet address
    deployment_count: Number of contracts deployed by this address
    avg_interval_hours: Average hours between deployments
    mixer_funded: True if deployer was funded via Tornado/mixer
    cex_funded: True if funded via centralized exchange
    source_verified: True if contract source is verified
    code_similarity: Cosine similarity to known scam bytecode (0-1)
    code_uniqueness: How unique the bytecode is (0-1, 1=completely unique)
    funding_chain_length: Number of hops from funding source
    rapid_deploy: True if >5 deploys in <6 hours
    cloned_contract: True if exact bytecode match to another deployed contract
    funded_by_scammer: True if funding came from known scam address

    Returns
    -------
    Dict with:
        - risk_score: int 0-100
        - risk_label: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
        - signals_found: List of triggered risk signals
        - mitigations: List of positive signals reducing risk
        - fingerprint_match: Closest known pattern match
        - summary: Human-readable one-liner
    """
    signals_found = []
    mitigations = []
    risk_score = 0.0

    # ── Evaluate each signal ──
    if deployment_count == 0:
        signals_found.append(SIGNALS["first_deployment"])
        risk_score += SIGNALS["first_deployment"]["weight"]

    if mixer_funded:
        signals_found.append(SIGNALS["mixer_funded"])
        risk_score += SIGNALS["mixer_funded"]["weight"]

    if cex_funded:
        mitigations.append(SIGNALS["cex_funded"])
        risk_score += SIGNALS["cex_funded"]["weight"]  # negative weight reduces score

    if not source_verified and deployment_count > 0:
        signals_found.append(SIGNALS["unverified_source"])
        risk_score += SIGNALS["unverified_source"]["weight"]

    if deployment_count > 10:
        signals_found.append(SIGNALS["batch_deployer"])
        risk_score += SIGNALS["batch_deployer"]["weight"]

    if code_similarity > 0.7:
        signals_found.append(SIGNALS["code_similar_to_scam"])
        risk_score += SIGNALS["code_similar_to_scam"]["weight"]

    if funded_by_scammer:
        signals_found.append(SIGNALS["funded_by_scammer"])
        risk_score += SIGNALS["funded_by_scammer"]["weight"]

    if cloned_contract:
        signals_found.append(SIGNALS["cloned_contract"])
        risk_score += SIGNALS["cloned_contract"]["weight"]

    if rapid_deploy:
        signals_found.append(SIGNALS["high_deployment_velocity"])
        risk_score += SIGNALS["high_deployment_velocity"]["weight"]

    # ── Behavioral fingerprint matching ──
    fingerprint = build_behavioral_fingerprint(
        address,
        deployment_count=deployment_count,
        avg_interval_hours=avg_interval_hours,
        mixer_funded=mixer_funded,
        source_verified=source_verified,
        code_uniqueness=code_uniqueness,
        funding_chain_length=funding_chain_length,
    )

    best_match = None
    best_similarity = 0.0
    for name, pattern in KNOWN_PATTERNS.items():
        pattern_vec = np.array(pattern["vector"], dtype=np.float32)
        # Cosine similarity between fingerprint and pattern
        sim = np.dot(fingerprint, pattern_vec) / (
            np.linalg.norm(fingerprint) * np.linalg.norm(pattern_vec) + 1e-8
        )
        if sim > best_similarity:
            best_similarity = float(sim)
            best_match = {
                "pattern": name,
                "similarity": round(best_similarity, 3),
                "description": pattern["description"],
                "risk_modifier": pattern["risk_modifier"],
            }

    if best_similarity > 0.7 and best_match and best_match["pattern"] != "legitimate_dev":
        signals_found.append(SIGNALS["known_scammer_match"])
        risk_score += SIGNALS["known_scammer_match"]["weight"]
        # Apply pattern-specific modifier
        risk_score *= 1.0 + best_match["risk_modifier"] * 0.3

    # ── Normalize to 0-100 ──
    risk_score = min(100, max(0, risk_score * 100))

    # ── Label ──
    if risk_score >= 80:
        label = "CRITICAL"
    elif risk_score >= 60:
        label = "HIGH"
    elif risk_score >= 35:
        label = "MEDIUM"
    else:
        label = "LOW"

    # ── Summary ──
    signal_count = len(signals_found)
    mitigation_count = len(mitigations)
    parts = []

    if signal_count == 0 and mitigation_count > 0:
        parts.append(f"Low risk ({risk_score:.0f}%) - strong positive signals")
    elif signal_count == 0:
        parts.append(f"Low risk ({risk_score:.0f}%) - insufficient data for assessment")
    elif risk_score >= 80:
        parts.append(f"CRITICAL risk ({risk_score:.0f}%) - {signal_count} warning signals")
    else:
        parts.append(f"{label} risk ({risk_score:.0f}%) - {signal_count} signals")

    if best_match and best_similarity > 0.5:
        parts.append(
            f"fingerprint matches '{best_match['description']}' (similarity: {best_similarity:.2f})"
        )

    if mitigations:
        mitigating = [m["description"] for m in mitigations]
        parts.append(f"mitigated by: {'; '.join(mitigating[:2])}")

    return {
        "risk_score": round(risk_score),
        "risk_label": label,
        "signals_found": [
            {"signal": s["description"], "risk": s["risk_level"], "weight": s["weight"]}
            for s in signals_found
        ],
        "mitigations": [{"signal": m["description"], "impact": m["weight"]} for m in mitigations],
        "fingerprint_match": best_match,
        "fingerprint_vector": fingerprint.tolist(),
        "summary": ". ".join(parts),
        "address": address,
    }

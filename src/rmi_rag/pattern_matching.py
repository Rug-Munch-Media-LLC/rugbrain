"""Pattern matching engine — compare a new token/contract against known scams.

Runs three deterministic comparisons against the historical scam-pattern
knowledge extracted by :mod:`rmi_rag.scam_patterns`:

1. **Bytecode similarity** — the token's contract code is fingerprinted with
   the same 128-dim structural feature extractor used for known patterns, and
   cosine-similarity scored against every pattern's code snippet.
2. **Deployer behavior** — rule checks for rug-pull deployer fingerprints
   (fresh wallet, mixer funding, rapid multi-deploy, multi-chain).
3. **Token economics** — rule checks for pump-dump / honeypot economics
   (sell tax, LP not locked, mint enabled, ownership not renounced,
   supply concentration).

Output shape (consumed by the API)::

    {
      "matched_patterns": [
        {"name", "confidence", "reference_incident", "technique"}
      ],
      "risk_signals": ["unlimited-mint", "ownership-not-renounced", ...]
    }
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rmi_rag.crypto_embeddings import KNOWN_SCAM_PATTERNS
from rmi_rag.feature_extractors import extract_contract_features
from rmi_rag.scam_patterns import (
    detect_indicators,
    get_scam_patterns,
)

logger = logging.getLogger(__name__)

# Confidence thresholds (0..1) for bytecode similarity.
BYTECODE_MATCH_THRESHOLD = 0.45
STRONG_BYTECODE_MATCH = 0.6

# Common Solidity tokens that carry no signal (excluded from overlap).
_STOPWORDS = {
    "function",
    "public",
    "private",
    "external",
    "internal",
    "contract",
    "interface",
    "library",
    "returns",
    "return",
    "memory",
    "calldata",
    "storage",
    "string",
    "uint",
    "uint256",
    "uint128",
    "uint64",
    "uint32",
    "address",
    "bool",
    "bytes",
    "bytes32",
    "require",
    "assert",
    "revert",
    "if",
    "else",
    "for",
    "while",
    "new",
    "this",
    "msg",
    "sender",
    "emit",
    "pragma",
    "solidity",
    "import",
    "using",
    "mapping",
    "struct",
    "enum",
    "modifier",
    "constructor",
    "payable",
    "pure",
    "view",
    "override",
    "virtual",
}


def _code_tokens(code: str) -> set[str]:
    """Extract distinctive identifier/opcode tokens from Solidity code.

    Drops the Solidity stopword list so overlap reflects scam-relevant
    structure (call sites, functions, state vars) rather than boilerplate.
    """
    import re as _re

    tokens = set(_re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", code))
    return {t for t in tokens if t not in _STOPWORDS}


def _token_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard-style overlap of distinctive code tokens (0..1)."""
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union)


def _cosine(a: NDArray[np.float32], b: NDArray[np.float32]) -> float:
    """Cosine similarity between two 1-D arrays (0.0 when either is empty)."""
    if a.size == 0 or b.size == 0:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _indicator_hits(code: str, indicators: list[str]) -> int:
    """Count how many indicator labels are present in *code*."""
    if not code or not indicators:
        return 0
    low = code.lower()
    return sum(1 for ind in indicators if ind.replace("-", " ").replace("_", " ") in low)


# ── 1. Bytecode / code-pattern matching ────────────────────────────────


def match_bytecode(
    contract_code: str,
    patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score *contract_code* against every pattern's code snippet.

    Uses a precision-oriented combination:
      * distinctive-code-token overlap (Jaccard over non-stopword tokens),
      * indicator keyword presence in the token code,
      * 128-dim structural-feature cosine as a secondary signal.

    Returns matches with ``confidence`` (0..1) and, when available, the
    pattern's strongest reference incident.
    """
    if not contract_code or len(contract_code) < 10:
        return []

    token_tokens = _code_tokens(contract_code)
    token_low = contract_code.lower()
    token_features = extract_contract_features(contract_code)
    matches: list[dict[str, Any]] = []

    for pattern in patterns:
        snippet = pattern.get("code_snippet") or ""
        snippet_tokens = _code_tokens(snippet)
        overlap = _token_overlap(token_tokens, snippet_tokens)

        # Indicator presence: count pattern indicator labels whose normalized
        # name (e.g. "reentrancy-guard-missing" → "reentrancy guard missing")
        # appears in the token code text.
        indicators = pattern.get("indicators") or []
        indicator_hits = 0
        for ind in indicators:
            label = ind.replace("-", " ").replace("_", " ")
            if label in token_low:
                indicator_hits += 1

        feature_sim = 0.0
        if snippet:
            pattern_features = extract_contract_features(snippet)
            feature_sim = _cosine(token_features, pattern_features)

        # Confidence: indicator presence is the strongest signal; token
        # overlap next; the dense feature cosine is a weak tiebreaker.
        if indicator_hits:
            confidence = min(1.0, 0.45 + 0.2 * indicator_hits)
        elif overlap >= 0.35:
            confidence = min(0.9, 0.5 + overlap * 0.6)
        elif feature_sim >= 0.85:
            # Only accept dense-feature matches when they are near-identical;
            # the 128-dim vector saturates on Solidity boilerplate.
            confidence = 0.5
        else:
            continue

        refs = pattern.get("reference_incidents") or []
        matches.append(
            {
                "name": pattern.get("attack_type") or pattern.get("pattern_id") or "unknown",
                "confidence": round(confidence, 3),
                "reference_incident": refs[0] if refs else "",
                "technique": pattern.get("technique") or "",
                "severity": pattern.get("severity") or "medium",
                "match_type": "bytecode",
            }
        )

    matches.sort(key=lambda m: m["confidence"], reverse=True)
    return matches


# ── 2. Deployer behavior rules ─────────────────────────────────────────


def match_deployer_behavior(deployer_metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Score deployer behavior against known rug-pull deployer fingerprints.

    Each signal contributes a deterministic confidence bump and a named
    risk signal. ``deployer_metrics`` keys: ``deployment_count``,
    ``mixer_funded``, ``rapid_deploy``, ``multi_chain``, ``age_hours``.
    """
    metrics = deployer_metrics or {}
    signals: list[dict[str, Any]] = []

    try:
        deploy_count = int(metrics.get("deployment_count") or 0)
    except (TypeError, ValueError):
        deploy_count = 0
    if deploy_count >= 3:
        signals.append(
            {
                "name": "serial-deployer",
                "confidence": min(0.9, 0.4 + 0.1 * deploy_count),
                "reference_incident": "serial rug-pull deployers deploy 3+ tokens, each rugged in days",
                "technique": "deployer-behavior",
                "severity": "high",
            }
        )

    if metrics.get("mixer_funded"):
        signals.append(
            {
                "name": "mixer-funded-deployer",
                "confidence": 0.85,
                "reference_incident": "rug deployers fund gas from mixers (Tornado Cash) to hide identity",
                "technique": "deployer-behavior",
                "severity": "critical",
            }
        )

    if metrics.get("rapid_deploy"):
        signals.append(
            {
                "name": "rapid-deploy-after-rug",
                "confidence": 0.8,
                "reference_incident": "same wallet redeploys within hours of a prior token collapse",
                "technique": "deployer-behavior",
                "severity": "high",
            }
        )

    if metrics.get("multi_chain"):
        signals.append(
            {
                "name": "multi-chain-launch",
                "confidence": 0.6,
                "reference_incident": "scammers launch the same token across chains to maximize victims",
                "technique": "deployer-behavior",
                "severity": "medium",
            }
        )

    try:
        age_hours = float(metrics.get("age_hours") or 0)
    except (TypeError, ValueError):
        age_hours = 0.0
    if 0 < age_hours < 24 and deploy_count >= 1:
        signals.append(
            {
                "name": "fresh-deployment",
                "confidence": 0.35,
                "reference_incident": "most rug pulls exit within 24-72h of deployment",
                "technique": "deployer-behavior",
                "severity": "medium",
            }
        )

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    return signals


# ── 3. Token economics rules ───────────────────────────────────────────


def match_token_economics(economics: dict[str, Any]) -> list[dict[str, Any]]:
    """Score token economics against known pump-dump / honeypot patterns.

    ``economics`` keys: ``sell_tax_pct``, ``lp_locked``, ``mint_enabled``,
    ``ownership_renounced``, ``holder_concentration_pct``.
    """
    econ = economics or {}
    signals: list[dict[str, Any]] = []

    try:
        sell_tax = float(econ.get("sell_tax_pct") or 0)
    except (TypeError, ValueError):
        sell_tax = 0.0

    if sell_tax >= 10:
        signals.append(
            {
                "name": "high-sell-tax",
                "confidence": min(0.9, 0.5 + sell_tax / 200),
                "reference_incident": "pump-dump tokens hide 10-50% sell taxes to trap buyers",
                "technique": "token-economics",
                "severity": "high",
            }
        )

    if not econ.get("lp_locked"):
        signals.append(
            {
                "name": "unlocked-liquidity",
                "confidence": 0.7,
                "reference_incident": "rugged tokens keep LP unlocked so the deployer can drain it",
                "technique": "token-economics",
                "severity": "critical",
            }
        )

    if econ.get("mint_enabled"):
        signals.append(
            {
                "name": "mint-enabled",
                "confidence": 0.75,
                "reference_incident": "unlimited mint dilutes holders to zero before liquidity drain",
                "technique": "token-economics",
                "severity": "critical",
            }
        )

    if not econ.get("ownership_renounced"):
        signals.append(
            {
                "name": "ownership-not-renounced",
                "confidence": 0.6,
                "reference_incident": "deployer keeps ownership to change fees/blacklist/mint later",
                "technique": "token-economics",
                "severity": "high",
            }
        )

    try:
        concentration = float(econ.get("holder_concentration_pct") or 0)
    except (TypeError, ValueError):
        concentration = 0.0
    if concentration >= 40:
        signals.append(
            {
                "name": "holder-concentration",
                "confidence": min(0.85, 0.5 + concentration / 200),
                "reference_incident": "top wallets holding 40%+ supply can dump and crash the price",
                "technique": "token-economics",
                "severity": "high",
            }
        )

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    return signals


# ── Orchestrator ───────────────────────────────────────────────────────


async def match_token_patterns(
    token_data: dict[str, Any],
    patterns: list[dict[str, Any]] | None = None,
    include_static: bool = True,
) -> dict[str, Any]:
    """Run the full pattern-matching pipeline for a token/contract.

    Args:
        token_data: Raw scanner/backend token dict (normalized internally by
            :func:`rmi_rag.scam_patterns.build_token_scam_profile`).
        patterns: Optional pre-loaded scam patterns (skips the cache lookup).
        include_static: When True (default), also score against the static
            ``KNOWN_SCAM_PATTERNS`` template list embedded in the RAG.

    Returns:
        ``{"matched_patterns": [...], "risk_signals": [...]}``
    """
    from rmi_rag.scam_patterns import build_token_scam_profile

    profile = build_token_scam_profile(token_data)
    contract_code = profile.get("contract_code") or ""
    code_indicators = detect_indicators(contract_code)

    if patterns is None:
        try:
            patterns = await get_scam_patterns()
        except Exception as e:
            logger.debug("pattern load failed, using static templates: %s", e)
            patterns = []

    all_patterns = list(patterns)
    if include_static:
        # Convert static templates into the same dict shape.
        static: list[dict[str, Any]] = [
            {
                "attack_type": str(p["name"]),
                "technique": str(p["name"]).lower().replace(" ", "-"),
                "code_snippet": "\n".join(str(s) for s in (p.get("code_snippets") or [])),
                "indicators": [str(i) for i in (p.get("indicators") or [])],
                "severity": str(p.get("severity", "high")),
                "reference_incidents": [],
            }
            for p in KNOWN_SCAM_PATTERNS
        ]
        all_patterns.extend(static)

    matched = match_bytecode(contract_code, all_patterns)
    matched.extend(match_deployer_behavior(profile.get("deployer_metrics") or {}))
    matched.extend(match_token_economics(profile.get("economics") or {}))

    # Deduplicate by name keeping the highest confidence.
    best_by_name: dict[str, dict[str, Any]] = {}
    for m in matched:
        name = m["name"]
        if name not in best_by_name or m["confidence"] > best_by_name[name]["confidence"]:
            best_by_name[name] = m
    matched = sorted(best_by_name.values(), key=lambda m: m["confidence"], reverse=True)

    # Risk signals: named indicators + top matched pattern names.
    risk_signals = list(code_indicators)
    for m in matched:
        label = m.get("name", "")
        if label and label not in risk_signals:
            risk_signals.append(label)
        technique = m.get("technique", "")
        if technique and technique not in risk_signals:
            risk_signals.append(technique)

    return {
        "matched_patterns": matched,
        "risk_signals": risk_signals[:25],
    }


def score_scam_risk(
    matched_patterns: list[dict[str, Any]], risk_signals: list[str]
) -> dict[str, Any]:
    """Convert matched patterns + signals into a summary risk verdict.

    Returns ``{"risk": "high|medium|low|unknown", "confidence": 0..1,
    "top_pattern": str}`` — used by the analysis endpoint to give an
    at-a-glance verdict without burying the detail.
    """
    if not matched_patterns:
        return {"risk": "unknown", "confidence": 0.0, "top_pattern": ""}

    max_conf = max(m["confidence"] for m in matched_patterns)
    critical_count = sum(1 for m in matched_patterns if m.get("severity") == "critical")
    high_count = sum(1 for m in matched_patterns if m.get("severity") in ("high", "critical"))
    top = max(matched_patterns, key=lambda m: m["confidence"])

    if max_conf >= 0.75 or critical_count >= 2 or len(risk_signals) >= 5:
        risk = "high"
    elif max_conf >= 0.45 or high_count >= 1 or len(risk_signals) >= 2:
        risk = "medium"
    else:
        risk = "low"

    return {
        "risk": risk,
        "confidence": round(min(1.0, max_conf + 0.05 * len(risk_signals)), 3),
        "top_pattern": top.get("name", ""),
    }


__all__ = [
    "match_bytecode",
    "match_deployer_behavior",
    "match_token_economics",
    "match_token_patterns",
    "score_scam_risk",
]

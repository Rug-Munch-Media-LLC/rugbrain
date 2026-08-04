"""Scam pattern extraction from ingested threat intel.

Turns raw ingested exploit/hack data (defihacklabs PoCs, SWC registry,
rekt-news reports — all already in the ``threat_intel`` Qdrant collection)
into structured, queryable scam patterns::

    {
        "pattern_id": "reentrancy:access-control",
        "attack_type": "reentrancy",
        "technique": "withdraw-after-external-call",
        "code_snippet": "...",
        "affected_protocols": ["Agave", "..."],
        "chain": "EVM",
        "severity": "critical",
        "funds_lost": 1_500_000,
        "reference_incidents": ["https://...", "..."],
        "indicators": ["call.value", "withdraw(...)"],
    }

The extractor is deterministic (no LLM) so it can run on every ingest
without cost; an optional LLM enrichment pass (``pattern_extractor``)
can be layered on top later. Patterns are stored in the ``scam_patterns``
Qdrant collection and cached in Redis for fast lookups.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from rmi_rag.qdrant_store import QdrantVectorStore

logger = logging.getLogger(__name__)

# ── Collection names ──────────────────────────────────────────────
THREAT_INTEL_COLLECTION = "threat_intel"
SCAM_PATTERNS_COLLECTION = "scam_patterns"

# Redis cache key for the aggregated pattern list (JSON).
PATTERN_CACHE_KEY = "rag:scam_patterns:cache"
PATTERN_CACHE_TTL = 86400  # 24h

# ── Attack-vector vocabulary (canonical, lowercase, hyphenated) ────
ATTACK_VECTORS = (
    "reentrancy",
    "flash-loan",
    "oracle-manipulation",
    "access-control",
    "signature-malleability",
    "bridge-exploit",
    "governance-attack",
    "integer-overflow",
    "withdrawal-exploit",
    "front-running",
    "phishing",
    "supply-chain",
    "key-compromise",
    "rug-pull",
    "pump-dump",
    "exit-scam",
    "honeypot",
    "smart-contract-exploit",
)

# Keyword → attack vector detection (deterministic, applied to code text).
VECTOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("reentrancy", ("reentrancy", "withdraw(", "call.value", "callback", "onTokenTransfer")),
    ("flash-loan", ("flashloan", "flash loan", "flash_loan", "flashBorrow", "borrow(", "aave")),
    ("oracle-manipulation", ("oracle", "spot price", "manipulat", "twap", "reserves")),
    ("access-control", ("onlyowner", "owner", "admin", "auth", "permission", "tx.origin")),
    ("signature-malleability", ("signature", "ecrecover", "malleab", "schnorr")),
    ("bridge-exploit", ("bridge", "wormhole", "multichain", "anycall", "router")),
    ("governance-attack", ("governance", "proposal", "vote", "quorum")),
    ("integer-overflow", ("uint", "overflow", "underflow", "safeMath", "unchecked")),
    ("withdrawal-exploit", ("withdraw", "payout", "claim", "redemption")),
    ("front-running", ("front-run", "pending", "mempool", "sandwich")),
    ("phishing", ("phish", "approve(", "permit(", "drain")),
    ("supply-chain", ("supply", "dependency", "npm", "upgrade", "proxy")),
    ("rug-pull", ("rug", "liquidity removed", "removeLiquidity", "honeypot")),
    ("pump-dump", ("pump", "dump", "whale", "insider", "bundl")),
)

# Indicator keywords → technique label (for risk signals on new tokens).
INDICATOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "reentrancy-guard-missing",
        ("call.value", "call{value", "low-level call", "delegatecall", "callback"),
    ),
    ("unchecked-external-call", ("transfer(", "send(", "call{value", "call.value")),
    ("unlimited-mint", ("function mint(", "onlyowner mint", "mint(", "supplycap")),
    ("ownership-not-renounced", ("onlyowner", "owner =", "transferownership", "renounceownership")),
    ("hidden-withdraw", ("withdraw", "manualswap", "rescuetoken", "sweep", "drain")),
    ("proxy-upgradeable", ("upgradeto", "upgradetoandcall", "delegatecall", "implementation")),
    ("approval-phishing", ("approve(", "approveall", "permit(", "transferfrom")),
    ("max-tx-limit", ("maxtx", "maxsell", "maxbuy", "maxwallet")),
    ("tax-manipulation", ("setfee", "settax", "taxfee", "liquidityfee", "fee =")),
    ("blacklist-control", ("blacklist", "addtoblacklist", "isblacklisted")),
)

# Severity ranking for aggregation.
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _normalize_vector(value: str | None) -> str:
    """Canonical attack-vector label (lowercase, hyphenated, default unknown)."""
    if not value:
        return "unknown"
    v = value.strip().lower().replace("_", "-").replace(" ", "-")
    # Map category names to canonical vectors when the vector is a category.
    aliases = {
        "smart-contract": "smart-contract-exploit",
        "smart-contract-exploit": "smart-contract-exploit",
        "business-logic": "smart-contract-exploit",
        "scam-pattern": "rug-pull",
    }
    return aliases.get(v, v)


def detect_attack_vectors(text: str) -> list[str]:
    """Deterministically detect attack vectors mentioned in *text*.

    Returns canonical vector labels ordered by keyword-match confidence
    (most keyword hits first). Falls back to ``["unknown"]`` when nothing
    matches.
    """
    low = text.lower() if text else ""
    hits: list[tuple[int, str]] = []
    for vector, keywords in VECTOR_KEYWORDS:
        count = sum(1 for kw in keywords if kw in low)
        if count > 0:
            hits.append((count, vector))
    # Sort by keyword-hit count descending; stable sort keeps vocabulary
    # order for ties (more specific vectors are listed earlier).
    hits.sort(key=lambda pair: pair[0], reverse=True)
    return [v for _, v in hits] or ["unknown"]


def detect_indicators(code: str) -> list[str]:
    """Return indicator labels present in *code* (deterministic keyword scan)."""
    if not code:
        return []
    low = code.lower()
    found: list[str] = []
    for label, keywords in INDICATOR_KEYWORDS:
        if any(kw in low for kw in keywords):
            found.append(label)
    return found


def _parse_funds_lost(value: Any) -> float:
    """Parse a funds-lost value (USD float, ``"$1.5M"`` string, or None) → float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    multiplier = 1.0
    match = re.search(r"([\d,\.]+)\s*([KMB])?", text, re.IGNORECASE)
    if not match:
        return 0.0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    suffix = (match.group(2) or "").upper()
    if suffix == "K":
        multiplier = 1_000
    elif suffix == "M":
        multiplier = 1_000_000
    elif suffix == "B":
        multiplier = 1_000_000_000
    return number * multiplier


def _max_severity(severities: list[str]) -> str:
    """Return the highest severity from a list of severity labels."""
    best = "info"
    best_rank = -1
    for sev in severities:
        rank = _SEVERITY_RANK.get(sev.strip().lower(), 0)
        if rank > best_rank:
            best_rank = rank
            best = sev.strip().lower()
    return best


def extract_scam_patterns_from_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate raw threat-intel docs into structured scam patterns.

    Groups docs by attack vector, then by technique (derived from
    indicators), and emits one pattern per group with the strongest code
    snippet, affected protocols, chain, severity, funds lost and reference
    incidents (source URLs).
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for doc in docs:
        metadata = doc.get("metadata", {}) or {}
        content = doc.get("content", "") or ""
        source_url = metadata.get("source_url") or doc.get("source", "")

        # Attack vector: prefer explicit metadata, else detect from code.
        vectors = detect_attack_vectors(content)
        vector = _normalize_vector(metadata.get("attack_vector") or metadata.get("category"))
        if vector in ("unknown", "smart-contract-exploit") and vectors[0] != "unknown":
            vector = vectors[0]

        indicators = detect_indicators(content)
        technique = indicators[0] if indicators else vector.replace("-", "-")

        key = (vector, technique)
        group = grouped.setdefault(
            key,
            {
                "pattern_id": f"{vector}:{technique}",
                "attack_type": vector,
                "technique": technique,
                "code_snippet": "",
                "affected_protocols": [],
                "chain": "",
                "severity": "info",
                "funds_lost": 0.0,
                "reference_incidents": [],
                "indicators": [],
                "doc_count": 0,
            },
        )

        protocol = str(metadata.get("protocol") or metadata.get("contract_name") or "").strip()
        if protocol and protocol not in group["affected_protocols"]:
            group["affected_protocols"].append(protocol)

        # Keep the longest code snippet (most representative PoC).
        if len(content) > len(group["code_snippet"]):
            group["code_snippet"] = content[:4000]

        chain = str(metadata.get("chain") or "").strip()
        if chain and not group["chain"]:
            group["chain"] = chain

        group["severity"] = _max_severity([group["severity"], doc.get("severity", "")])

        funds = _parse_funds_lost(metadata.get("amount_lost_usd") or metadata.get("amount_lost"))
        group["funds_lost"] = max(group["funds_lost"], funds)

        if source_url and source_url not in group["reference_incidents"]:
            group["reference_incidents"].append(str(source_url))

        for indicator in indicators:
            if indicator not in group["indicators"]:
                group["indicators"].append(indicator)

        group["doc_count"] += 1

    patterns = list(grouped.values())
    # Sort by severity then by funds lost, so the most dangerous patterns
    # come first when they are displayed to users.
    patterns.sort(
        key=lambda p: (_SEVERITY_RANK.get(p["severity"], 0), p["funds_lost"]),
        reverse=True,
    )
    return patterns


async def extract_scam_patterns_from_intel(
    limit: int = 1000,
    collection: str = THREAT_INTEL_COLLECTION,
) -> list[dict[str, Any]]:
    """Scroll the threat-intel collection and aggregate into scam patterns."""
    store = QdrantVectorStore()
    docs: list[dict[str, Any]] = []
    offset: str | None = None
    try:
        while len(docs) < limit:
            batch, offset = await store.scroll(
                collection, limit=min(500, limit - len(docs)), offset=offset
            )
            if not batch:
                break
            docs.extend(batch)
            if not offset:
                break
    finally:
        await store.close()

    logger.info("scam_patterns: read %d docs from %s", len(docs), collection)
    return extract_scam_patterns_from_docs(docs)


async def get_scam_patterns(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return the aggregated scam-pattern list.

    Checks the Redis cache first (24h TTL); on miss, re-extracts from the
    threat-intel collection and refreshes the cache. ``force_refresh``
    bypasses the cache entirely.
    """
    from typing import cast

    from rmi_rag.search_service import _get_redis

    if not force_refresh:
        try:
            r = await _get_redis()  # type: ignore[no-untyped-call]
            cached = await r.get(PATTERN_CACHE_KEY)
            if cached:
                return cast("list[dict[str, Any]]", json.loads(cached))
        except Exception as e:
            logger.debug("scam_patterns cache read failed: %s", e)

    patterns = await extract_scam_patterns_from_intel()

    try:
        r = await _get_redis()  # type: ignore[no-untyped-call]
        await r.setex(PATTERN_CACHE_KEY, PATTERN_CACHE_TTL, json.dumps(patterns))
    except Exception as e:
        logger.debug("scam_patterns cache write failed: %s", e)

    return patterns


async def store_scam_patterns(patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """Store structured scam patterns into the ``scam_patterns`` collection.

    Uses the standard semantic embedding (1024-dim bge-m3 via Infinity) so
    the collection stays queryable by the same embedder as the rest of the
    RAG. Returns ``{"stored": N, "skipped": M}``.
    """
    from rmi_rag.embeddings import get_embedding

    store = QdrantVectorStore()
    stored = 0
    skipped = 0
    try:
        for pattern in patterns:
            content = (
                f"SCAM PATTERN: {pattern['attack_type']} ({pattern['technique']})\n"
                f"Protocols: {', '.join(pattern['affected_protocols'][:10])}\n"
                f"Chain: {pattern['chain']} | Severity: {pattern['severity']}\n"
                f"Funds lost: ${pattern['funds_lost']:,.0f}\n"
                f"Indicators: {', '.join(pattern['indicators'][:12])}\n"
                f"Reference incidents: {', '.join(pattern['reference_incidents'][:5])}"
            )
            try:
                embedding = await get_embedding(content)
            except Exception as e:
                logger.warning("scam_patterns embed failed for %s: %s", pattern["pattern_id"], e)
                skipped += 1
                continue

            doc_id = f"scam_pattern:{pattern['pattern_id']}"
            ok = await store.insert(
                doc_id=doc_id,
                collection=SCAM_PATTERNS_COLLECTION,
                embedding=embedding,
                content=content,
                metadata={
                    "pattern_id": pattern["pattern_id"],
                    "attack_type": pattern["attack_type"],
                    "technique": pattern["technique"],
                    "severity": pattern["severity"],
                    "chain": pattern["chain"],
                    "funds_lost": pattern["funds_lost"],
                    "affected_protocols": pattern["affected_protocols"][:20],
                    "reference_incidents": pattern["reference_incidents"][:20],
                    "indicators": pattern["indicators"][:20],
                    "doc_count": pattern["doc_count"],
                },
                source="scam_pattern_extractor",
                severity=pattern["severity"],
                chain=pattern["chain"],
            )
            if ok:
                stored += 1
            else:
                skipped += 1
    finally:
        await store.close()

    logger.info("scam_patterns stored=%d skipped=%d", stored, skipped)
    return {"stored": stored, "skipped": skipped, "patterns": len(patterns)}


def build_token_scam_profile(token_data: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized scam-analysis profile from raw scanner token data.

    Normalizes the many field shapes the scanner/backend can send (nested
    ``metadata``, top-level keys, ``contract_code`` vs ``code``) into one
    dict consumed by the pattern-matching engine.
    """
    metadata = token_data.get("metadata") or {}
    merged = {**metadata, **{k: v for k, v in token_data.items() if k != "metadata"}}
    return {
        "address": merged.get("address") or merged.get("token_address") or "",
        "chain": merged.get("chain") or "ethereum",
        "name": merged.get("name") or merged.get("token_name") or "",
        "symbol": merged.get("symbol") or merged.get("token_symbol") or "",
        "contract_code": merged.get("contract_code")
        or merged.get("code")
        or merged.get("source_code")
        or "",
        "description": merged.get("description") or merged.get("summary") or "",
        "deployer_address": merged.get("deployer_address") or merged.get("creator_address") or "",
        "deployer_metrics": {
            "deployment_count": merged.get("deployment_count") or merged.get("deploy_count") or 0,
            "mixer_funded": bool(merged.get("mixer_funded") or merged.get("funded_by_mixer")),
            "rapid_deploy": bool(merged.get("rapid_deploy") or merged.get("rapid_deployment")),
            "multi_chain": bool(
                merged.get("multi_chain") or merged.get("deployed_multiple_chains")
            ),
            "age_hours": merged.get("age_hours") or merged.get("token_age_hours") or 0,
        },
        "economics": {
            "sell_tax_pct": merged.get("sell_tax_pct")
            or merged.get("sell_tax")
            or merged.get("tax")
            or 0,
            "lp_locked": bool(merged.get("lp_locked")),
            "mint_enabled": bool(merged.get("mint_enabled") or merged.get("can_mint")),
            "ownership_renounced": bool(merged.get("ownership_renounced")),
            "holder_concentration_pct": merged.get("holder_concentration_pct")
            or merged.get("top10_holder_pct")
            or 0,
        },
    }


__all__ = [
    "ATTACK_VECTORS",
    "SCAM_PATTERNS_COLLECTION",
    "THREAT_INTEL_COLLECTION",
    "build_token_scam_profile",
    "detect_attack_vectors",
    "detect_indicators",
    "extract_scam_patterns_from_docs",
    "extract_scam_patterns_from_intel",
    "get_scam_patterns",
    "store_scam_patterns",
]

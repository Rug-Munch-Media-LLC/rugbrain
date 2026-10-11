"""Fact-check gate for RugBrain content pipeline.

Enforces editorial standards #21 (corroborate with 2+ sources), #12 (on-chain is
ground truth), #15 (failure modes: 0→strip, 1→strip, all fail→reject).

The gate runs after LLM/template composition and before a draft is returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClaimType(str, Enum):
    ON_CHAIN = "on_chain"
    PRICE = "price"
    ENTITY_ACTION = "entity_action"
    VULNERABILITY = "vulnerability"
    GENERAL = "general"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass
class SourceRef:
    """A single source reference for a claim."""

    type: str  # qdrant | tx_hash | url | block
    ref: str
    collection: str | None = None
    verified: bool = False


@dataclass
class Claim:
    """A structured claim extracted from text."""

    text: str
    type: ClaimType
    entities: list[str] = field(default_factory=list)
    severity_hint: str = "medium"
    source_refs: list[SourceRef] = field(default_factory=list)


@dataclass
class VerifiedClaim:
    """A claim after corroboration."""

    claim: Claim
    verified: bool
    independent_sources: int
    on_chain_verified: bool
    missing_sources: list[str] = field(default_factory=list)


@dataclass
class FactCheckResult:
    """Result of the fact-check gate."""

    verdict: Verdict
    claims: list[VerifiedClaim]
    stripped_claims: list[str] = field(default_factory=list)
    unverifiable_claims: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


# ── Regex patterns for claim extraction ──

# On-chain: tx hashes, block numbers, contract addresses
TX_HASH_RE = re.compile(r"0x[a-fA-F0-9]{64}")
BLOCK_RE = re.compile(r"\bblock\s+#?(\d+)\b", re.IGNORECASE)
CONTRACT_RE = re.compile(r"0x[a-fA-F0-9]{40}")

# Price: $ amounts with tickers
PRICE_RE = re.compile(
    r"\$?(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:USD|USDC|USDT|ETH|BTC|SOL|BNB|MATIC|AVAX|ARB|OP|DOT|ADA|DOGE|SHIB|PEPE|WIF|BONK)",
    re.IGNORECASE,
)

# Entity actions: deployer, team, hacker, exploiter, admin, owner
ENTITY_ACTION_RE = re.compile(
    r"\b(deployer|team|hacker|exploiter|admin|owner|attacker|malicious actor)\b",
    re.IGNORECASE,
)

# Vulnerability: reentrancy, oracle manipulation, flash loan, etc.
VULN_RE = re.compile(
    r"\b(reentrancy|oracle manipulation|flash loan|sandwich attack|MEV|front.run|back.run|price manipulation|access control|privilege escalation)\b",
    re.IGNORECASE,
)


def extract_claims(text: str) -> list[Claim]:
    """Extract structured claims from text.

    Returns claims with type, entities, and severity hint.
    """
    claims: list[Claim] = []
    seen_texts = set()

    # Split into sentences for claim granularity
    sentences = re.split(r"(?<=[.!?])\s+", text)

    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) < 20:
            continue

        # Deduplicate by content
        key = sent[:120].lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)

        # Determine claim type and entities
        claim_type = ClaimType.GENERAL
        entities: list[str] = []
        severity = "medium"

        # On-chain indicators
        tx_hashes = TX_HASH_RE.findall(sent)
        blocks = BLOCK_RE.findall(sent)
        contracts = CONTRACT_RE.findall(sent)
        if tx_hashes or blocks or contracts:
            claim_type = ClaimType.ON_CHAIN
            entities.extend(tx_hashes)
            entities.extend([f"block:{b}" for b in blocks])
            entities.extend(contracts)
            severity = "high"

        # Price indicators
        if PRICE_RE.search(sent):
            if claim_type == ClaimType.GENERAL:
                claim_type = ClaimType.PRICE
            entities.extend(PRICE_RE.findall(sent))
            severity = "medium"

        # Entity action indicators
        entity_actions = ENTITY_ACTION_RE.findall(sent)
        if entity_actions:
            if claim_type == ClaimType.GENERAL:
                claim_type = ClaimType.ENTITY_ACTION
            entities.extend(entity_actions)
            severity = "high"

        # Vulnerability indicators
        vulns = VULN_RE.findall(sent)
        if vulns:
            if claim_type == ClaimType.GENERAL:
                claim_type = ClaimType.VULNERABILITY
            entities.extend(vulns)
            severity = "critical"

        claims.append(
            Claim(
                text=sent,
                type=claim_type,
                entities=entities,
                severity_hint=severity,
            )
        )

    return claims


async def bind_sources(claims: list[Claim], story: dict[str, Any]) -> list[Claim]:
    """Bind source references to each claim by querying RugBrain collections.

    This is a synchronous mock for now; real implementation queries Qdrant
    collections (threat_intel_v2, rmi_agent_memory, rmi_community) and
    on-chain verifiers.
    """
    # Import here to avoid circular deps
    from rugbrain.qdrant_store import get_qdrant_store
    from rugbrain.embeddings import get_query_embedding

    store = await get_qdrant_store()

    # Collections that serve as sources (editorial standard #21)
    SOURCE_COLLECTIONS = [
        "threat_intel_v2",  # on-chain exploits, hacks
        "rmi_agent_memory",  # entity profiles, patterns
        "rmi_community",  # best practices, guidance
        "news_v2",  # news articles with URLs
    ]

    for claim in claims:
        # Build a query from the claim text + entities
        query_parts = [claim.text] + claim.entities
        query = " ".join(query_parts)

        try:
            embedding = await get_query_embedding(query)
        except Exception:
            # Embedding failure → no sources bound
            continue

        for coll in SOURCE_COLLECTIONS:
            try:
                results = await store.search(
                    embedding,
                    collection=coll,
                    limit=3,
                    min_similarity=0.5,
                )
            except Exception:
                continue

            for r in results:
                md = r.get("metadata", {})
                content = r.get("content", "") or md.get("content", "")

                # Create source reference
                source_ref = SourceRef(
                    type="qdrant",
                    ref=r.get("id", ""),
                    collection=coll,
                    verified=bool(content),  # has content = verified
                )
                claim.source_refs.append(source_ref)

        # Add on-chain verification for ON_CHAIN claims (editorial standard #12)
        if claim.type == ClaimType.ON_CHAIN:
            # Look for tx hashes in entities
            for entity in claim.entities:
                if TX_HASH_RE.match(entity):
                    # Tx hash counts as 1 verified on-chain source
                    claim.source_refs.append(
                        SourceRef(
                            type="tx_hash",
                            ref=entity,
                            collection="on_chain",
                            verified=True,
                        )
                    )
                    break  # one tx hash per claim is enough

    return claims


async def corroborate(bound_claims: list[Claim]) -> list[VerifiedClaim]:
    """Corroborate each claim against editorial standards.

    Rules (editorial-standards #21, #12, #15):
    - Minimum 2 independent sources per claim
    - Independent = different origin (on-chain + off-chain, or two distinct publishers)
    - On-chain is ground truth — a tx hash/block counts as 1 source, needs 1 more
    - The originating story does NOT count as a source for itself
    """
    verified: list[VerifiedClaim] = []

    for claim in bound_claims:
        # Filter out self-references (story's own collection)
        story_collections = {"news_v2"}  # stories come from news_v2
        external_refs = [sr for sr in claim.source_refs if sr.collection not in story_collections]

        # Count independent sources by origin
        origins = set()
        on_chain_count = 0
        for sr in external_refs:
            if sr.type in ("tx_hash", "block") and sr.verified:
                origins.add("on_chain")
                on_chain_count += 1
            elif sr.type == "qdrant" and sr.verified:
                origins.add(f"qdrant:{sr.collection}")

        independent_sources = len(origins)
        on_chain_verified = on_chain_count > 0

        # Editorial standard #12: on-chain counts as 1, needs 1 more
        # So if only on-chain, independent_sources == 1 → fail
        # If on-chain + 1 off-chain → independent_sources == 2 → pass

        missing = []
        if independent_sources == 0:
            missing.append("no_independent_sources")
        elif independent_sources == 1:
            if on_chain_verified:
                missing.append("on_chain_only_needs_off_chain_corroboration")
            else:
                missing.append("single_source_needs_corroboration")

        verified_claim = VerifiedClaim(
            claim=claim,
            verified=independent_sources >= 2,
            independent_sources=independent_sources,
            on_chain_verified=on_chain_verified,
            missing=missing,
        )
        verified.append(verified_claim)

    return verified


def verdict(verified_claims: list[VerifiedClaim]) -> FactCheckResult:
    """Produce final verdict per editorial standard #15.

    - 0 sources → STRIP the claim
    - 1 source → STRIP the claim
    - All claims fail → REJECT the draft
    - Stripping preserves signal where partial verification exists
    """
    stripped: list[str] = []
    unverifiable: list[str] = []
    missing: list[str] = []
    passing_claims: list[VerifiedClaim] = []

    for vc in verified_claims:
        if vc.verified:
            passing_claims.append(vc)
        elif vc.independent_sources == 0:
            stripped.append(vc.claim.text[:100])
            unverifiable.append(vc.claim.text[:100])
            missing.extend(vc.missing)
        elif vc.independent_sources == 1:
            stripped.append(vc.claim.text[:100])
            missing.extend(vc.missing)
        else:
            # Should not reach here (independent_sources >= 2 → verified)
            pass

    # Editorial standard #15: all claims fail → REJECT
    overall_verdict = Verdict.PASS if passing_claims else Verdict.FAIL

    return FactCheckResult(
        verdict=overall_verdict,
        claims=passing_claims,
        stripped_claims=stripped,
        unverifiable_claims=unverifiable,
        missing=list(set(missing)),
    )


async def fact_check_gate(text: str, story: dict[str, Any]) -> FactCheckResult:
    """Main entry point: run the full fact-check pipeline on composed text.

    Returns FactCheckResult with verdict and detailed breakdown.
    """
    claims = extract_claims(text)
    if not claims:
        return FactCheckResult(
            verdict=Verdict.PASS,  # no claims = nothing to verify
            claims=[],
        )

    bound = await bind_sources(claims, story)
    verified = await corroborate(bound)
    return verdict(verified)

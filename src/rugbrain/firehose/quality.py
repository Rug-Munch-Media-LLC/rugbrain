"""Document quality scoring and entity extraction (pure functions).

Extracted from the former ``IngestionPipeline`` methods so the pipeline
class stays small; behavior is identical.
"""

import hashlib
import re
from typing import Any

__all__ = ["extract_entities", "make_content_hash", "score_quality"]


def make_content_hash(content: str) -> str:
    """Deterministic hash for dedup."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def score_quality(content: str, metadata: dict[str, Any]) -> int:
    """Score document quality 0-100. Skip low-signal content."""
    score = 50

    if len(content) > 500:
        score += 15
    elif len(content) > 200:
        score += 10
    elif len(content) < 50:
        score -= 20

    entity_count = 0
    if metadata.get("address"):
        entity_count += 1
    if metadata.get("chain"):
        entity_count += 1
    if metadata.get("token"):
        entity_count += 1
    if metadata.get("protocol"):
        entity_count += 1
    score += entity_count * 5

    high_authority = {"etherscan", "chainabuse", "rekt", "certik", "slowmist", "peckshield"}
    if metadata.get("source", "").lower() in high_authority:
        score += 20

    if metadata.get("severity") == "critical":
        score += 15
    elif metadata.get("severity") == "high":
        score += 10

    if metadata.get("is_variant"):
        score -= 10

    return max(0, min(100, score))


def extract_entities(content: str) -> dict[str, Any]:
    """Extract addresses, chains, tokens from content."""
    entities: dict[str, Any] = {"addresses": [], "chains": [], "tokens": [], "protocols": []}

    evm_addrs = re.findall(r"0x[a-fA-F0-9]{40}", content)
    entities["addresses"].extend(evm_addrs[:10])

    sol_addrs = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", content)
    entities["addresses"].extend([a for a in sol_addrs[:10] if a not in entities["addresses"]])

    known_chains = [
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "solana",
        "base",
        "fantom",
        "gnosis",
        "celo",
        "zksync",
        "linea",
        "scroll",
        "mantle",
        "sui",
        "aptos",
        "near",
        "tron",
        "bitcoin",
    ]
    for chain in known_chains:
        if chain.lower() in content.lower():
            entities["chains"].append(chain)

    tokens = re.findall(r"\$([A-Z]{2,10})", content)
    entities["tokens"].extend(tokens[:10])

    known_protocols = [
        "uniswap",
        "aave",
        "curve",
        "balancer",
        "sushi",
        "pancake",
        "raydium",
        "jupiter",
        "orca",
        "marinade",
        "lido",
        "eigenlayer",
        "compound",
        "maker",
        "yearn",
        "convex",
    ]
    for proto in known_protocols:
        if proto.lower() in content.lower():
            entities["protocols"].append(proto)

    return entities

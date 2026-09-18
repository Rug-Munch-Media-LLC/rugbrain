"""Technical depth: blockchain-mechanics term density scoring."""

from __future__ import annotations

import re

from rugbrain.news_rating.axes import _clamp

TECH_TERMS: tuple[str, ...] = (
    "smart contract",
    "liquidity pool",
    "total value locked",
    "tvl",
    "governance",
    "vault",
    "staking",
    "yield",
    "impermanent loss",
    "oracle",
    "reentrancy",
    "gas fee",
    "gas price",
    "layer 2",
    "rollup",
    "zero-knowledge",
    "zk",
    "audit",
    "tokenomics",
    "supply schedule",
    "lockup",
    "vesting",
    "apr",
    "apy",
    "collateral",
    "liquidation",
    "delegation",
    "consensus",
    "proof of stake",
    "proof of work",
    "hashrate",
    "multisig",
    "amm",
    "swap",
    "bridge",
    "sidechain",
    "whale",
    "exchange netflow",
    "open interest",
    "funding rate",
    "block reward",
    "halving",
    "token unlock",
    "airdrop",
    "depeg",
    "stablecoin",
    "flash loan",
)
_TECH_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(term) for term in sorted(TECH_TERMS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def technical_depth_score(text: str) -> int:
    """0-100: blockchain-mechanics explanation density.

    Short articles (< 150 words) are capped at 60 — mechanics need room
    to breathe.
    """
    words = len((text or "").split())
    if words < 10:
        return 0
    hits = len(_TECH_RE.findall(text or ""))
    hits_per_100 = hits / words * 100
    score = _clamp(hits_per_100 * 40)
    if words < 150:
        score = min(score, 60)
    return score

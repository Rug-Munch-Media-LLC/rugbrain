"""Canonical vocabularies for scam-pattern extraction.

Data only -- no functions. Three tables, three jobs:

* ``ATTACK_VECTORS``  the canonical lowercase hyphenated vector labels.
* ``VECTOR_KEYWORDS`` keyword -> attack-vector, for scanning ingested text.
* ``INDICATOR_KEYWORDS`` keyword -> technique label, for risk signals on new
  tokens.

Both keyword tables are applied deterministically to code text, with no LLM, so
they can run on every ingest at zero cost. The matching logic lives in
:mod:`rugbrain.scam_detect`.
"""

from __future__ import annotations

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

# Category labels that actually mean a canonical attack vector.
VECTOR_ALIASES: dict[str, str] = {
    "smart-contract": "smart-contract-exploit",
    "smart-contract-exploit": "smart-contract-exploit",
    "business-logic": "smart-contract-exploit",
    "scam-pattern": "rug-pull",
}

# Severity ranking for aggregation, weakest first.
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# Data module: __all__ still names every table explicitly so a table dropped
# during a refactor cannot leave a phantom import behind.
__all__ = [
    "ATTACK_VECTORS",
    "INDICATOR_KEYWORDS",
    "SEVERITY_RANK",
    "VECTOR_ALIASES",
    "VECTOR_KEYWORDS",
]

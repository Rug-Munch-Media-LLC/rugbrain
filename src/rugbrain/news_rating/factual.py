"""Factual density: verifiable-claim extraction and density scoring."""

from __future__ import annotations

import re

from rugbrain.news_rating.axes import _clamp

_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_TX_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")
_SOLANA_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_AMOUNT_USD_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?[kmb]?\b", re.IGNORECASE)
_AMOUNT_TOKEN_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:ETH|BTC|SOL|USDT|USDC|BNB|XRP|ADA|DOGE|AVAX|MATIC|LINK|DOT|UNI|DAI|ARB|OP|TON|TRX|LTC|BCH|ATOM|NEAR|APT|SUI)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%")

PROTOCOL_NAMES: tuple[str, ...] = (
    "uniswap",
    "aave",
    "compound",
    "curve",
    "lido",
    "makerdao",
    "gmx",
    "jupiter",
    "raydium",
    "marinade",
    "jito",
    "metaplex",
    "opensea",
    "blur",
    "balancer",
    "sushiswap",
    "pancakeswap",
    "1inch",
    "dydx",
    "synthetix",
    "yearn",
    "convex",
    "rocket pool",
    "eigenlayer",
    "arbitrum",
    "optimism",
    "base",
    "polygon",
    "avalanche",
    "solana",
    "ethereum",
    "bitcoin",
    "tether",
    "chainlink",
    "pyth",
    "wormhole",
    "layerzero",
    "hyperliquid",
    "tornado cash",
    "euler",
    "mango",
    "platypus",
    "radiant",
    "venus",
    "benqi",
    "cream",
    "iron bank",
    "rari",
    "fei",
    "olympus",
    "terra",
    "luna",
    "ftt",
    "alameda",
    "ftx",
    "celsius",
    "blockfi",
    "voyager",
    "genesis",
    "harmony",
    "ronin",
    "axie",
    "nomad",
    "wintermute",
    "liquidity",
    "collateral",
    "multisig",
    "smart contract",
    "tokenomics",
)
_PROTOCOL_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(name) for name in sorted(PROTOCOL_NAMES, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _find_claims(text: str) -> dict[str, list[str]]:
    """Return verifiable claim categories found in *text* (deduplicated)."""
    claims: dict[str, list[str]] = {
        "addresses": [],
        "tx_hashes": [],
        "solana_addresses": [],
        "amounts_usd": [],
        "amounts_token": [],
        "percentages": [],
        "protocols": [],
    }

    def _add(key: str, value: str) -> None:
        if value not in claims[key]:
            claims[key].append(value)

    for match in _ADDRESS_RE.findall(text):
        _add("addresses", match.lower())
    for match in _TX_HASH_RE.findall(text):
        _add("tx_hashes", match.lower())
    for match in _SOLANA_RE.findall(text):
        # Skip strings already counted as EVM addresses/tx hashes.
        if match.lower() not in claims["addresses"] and match.lower() not in claims["tx_hashes"]:
            _add("solana_addresses", match)
    for match in _AMOUNT_USD_RE.findall(text):
        _add("amounts_usd", match.strip())
    for match in _AMOUNT_TOKEN_RE.findall(text):
        _add("amounts_token", match.strip())
    for match in _PERCENT_RE.findall(text):
        _add("percentages", match)
    for match in _PROTOCOL_RE.findall(text):
        _add("protocols", match.lower())
    return claims


def factual_density_score(text: str) -> int:
    """0-100: verifiable claims per 100 words.

    Articles with zero claims are treated as opinion/promotional copy and
    score a small base; each claim per 100 words adds ~30 points (capped).
    """
    words = len((text or "").split())
    if words < 10:
        return 0
    claims = _find_claims(text or "")
    total = sum(len(v) for v in claims.values())
    if total == 0:
        return 15
    claims_per_100 = total / words * 100
    return _clamp(claims_per_100 * 30)

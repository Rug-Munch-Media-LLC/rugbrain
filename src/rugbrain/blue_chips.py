"""Verified blue-chip contract registry.

A risk verdict must rest on evidence ABOUT the address. For a handful of
canonical stablecoin, wrapped-asset and blue-chip DeFi contracts the
third-party label corpora hold no address-scoped row at all, so a classifier
that reads "no label" as suspicion calls a $30B contract a scam. This registry
records the canonical deployment per chain with its issuer and the public
source that establishes it, so the verdict can state *why* it is low.

Membership NEVER overrides address-scoped negative evidence: a blue chip that
is later labelled malicious still scores high (see ``scam_verdict``).

Only verifiably canonical contracts belong here. A token that merely shares a
symbol does not — symbol collisions are the common scam vector.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class BlueChip(NamedTuple):
    """One canonical contract: who issues it and how it is established."""

    symbol: str
    issuer: str
    evidence: str


#: Canonical Ethereum-mainnet deployments. Checksummed as published by the
#: issuer; lookup is case-insensitive.
VERIFIED_BLUE_CHIPS: Final[dict[str, BlueChip]] = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": BlueChip(
        "USDC", "Circle", "circle.com USDC contract address; Etherscan-verified"
    ),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": BlueChip(
        "USDT", "Tether", "tether.to contract address; Etherscan-verified"
    ),
    "0x6b175474e89094c44da98b954eedeac495271d0f": BlueChip(
        "DAI", "Sky/MakerDAO", "makerdao/dai contract address; Etherscan-verified"
    ),
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": BlueChip(
        "WETH", "Wrapped Ether", "canonical WETH9 deployment; Etherscan-verified"
    ),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": BlueChip(
        "WBTC", "BitGo", "wbtc.network contract address; Etherscan-verified"
    ),
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": BlueChip(
        "UNI", "Uniswap", "uniswap.org UNI token address; Etherscan-verified"
    ),
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": BlueChip(
        "AAVE", "Aave", "aave.com AAVE token address; Etherscan-verified"
    ),
    "0x514910771af9ca656af840dff83e8264ecf986ca": BlueChip(
        "LINK", "Chainlink", "chain.link LINK token address; Etherscan-verified"
    ),
}


def lookup_blue_chip(address: str) -> BlueChip | None:
    """Return the registry entry for *address*, or ``None`` when unlisted.

    Case-insensitive: an EIP-55 checksummed address and its lower-case form are
    the same account, and a verdict must not depend on which one a caller sent.
    """
    return VERIFIED_BLUE_CHIPS.get(str(address or "").strip().lower())


__all__ = ["VERIFIED_BLUE_CHIPS", "BlueChip", "lookup_blue_chip"]

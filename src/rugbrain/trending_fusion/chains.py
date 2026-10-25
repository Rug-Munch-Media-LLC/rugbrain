"""Chain registry for the one trending engine.

One row per supported chain. The scorer is identical for every chain; only the
adapter (candidate discovery, quote asset, native token) differs. Adding a chain
means adding a row here -- never a second formula.
"""

from __future__ import annotations

from dataclasses import dataclass

DEXSCREENER = "dexscreener"
DISCOVERY = "dexscreener_token_profiles+token_boosts"


@dataclass(frozen=True)
class ChainSpec:
    """Everything the shared scorer needs to run on one chain."""

    name: str
    aliases: tuple[str, ...]
    native_token: str
    candidate_source: str
    quote_asset: str
    data_source: str


def _spec(name: str, aliases: tuple[str, ...], native: str, quote: str) -> ChainSpec:
    return ChainSpec(name, aliases, native, DISCOVERY, quote, DEXSCREENER)


CHAINS: dict[str, ChainSpec] = {
    spec.name: spec
    for spec in (
        _spec("solana", ("sol", "spl", "solana-mainnet"), "SOL", "USDC"),
        _spec("ethereum", ("eth", "mainnet", "ethereum-mainnet"), "ETH", "USDC/WETH"),
        _spec("bsc", ("bnb", "binance", "binance-smart-chain"), "BNB", "USDT/WBNB"),
        _spec("base", ("base-mainnet",), "ETH", "USDC/WETH"),
        _spec("arbitrum", ("arb", "arbitrum-one"), "ETH", "USDC/WETH"),
        _spec("polygon", ("matic", "polygon-pos"), "POL", "USDC/WPOL"),
        _spec("optimism", ("op", "optimism-mainnet"), "ETH", "USDC/WETH"),
        _spec("avalanche", ("avax", "avalanche-c-chain"), "AVAX", "USDC/WAVAX"),
    )
}


def resolve_chain(name: str) -> ChainSpec | None:
    """Resolve a chain name or alias to its spec; ``None`` when unsupported."""
    key = (name or "").strip().lower()
    if key in CHAINS:
        return CHAINS[key]
    for spec in CHAINS.values():
        if key in spec.aliases:
            return spec
    return None

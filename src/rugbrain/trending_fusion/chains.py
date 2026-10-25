"""Chain registry for the one trending engine.

One row per supported chain. The scorer is identical for every chain; only the
adapter (candidate discovery, quote asset, native token) differs. Adding a chain
means adding a row here -- never a second formula.
"""

from __future__ import annotations

from dataclasses import dataclass

DEXSCREENER = "dexscreener"
DISCOVERY = "dexscreener_token_profiles+token_boosts"
# Real, verified discovery adapters (see geckoterminal.py / indexed_pairs.py):
#   geckoterminal: GET https://api.geckoterminal.com/api/v2/networks/{slug}/trending_pools?include=base_token
#                  rows <- data[].attributes.{volume_usd.h24,reserve_in_usd,txns,price_change_pct} + included[token].symbol
#   rmi_indexed:   GET http://127.0.0.1:8000/api/v1/market/trending?chain={chain}&limit=50
#                  rows <- token/volume_usd/liquidity/trades/price_change_pct (parquet_source FIELDS)
GT_NETWORKS = {
    "solana": "solana", "ethereum": "eth", "bsc": "bsc", "base": "base",
    "arbitrum": "arbitrum", "polygon": "polygon_pos", "optimism": "optimism", "avalanche": "avalanche",
}


@dataclass(frozen=True)
class ChainSpec:
    """Everything the shared scorer needs to run on one chain."""

    name: str
    aliases: tuple[str, ...]
    native_token: str
    candidate_source: str
    quote_asset: str
    data_source: str
    sources: tuple[str, ...] = ()


def _spec(name: str, aliases: tuple[str, ...], native: str, quote: str) -> ChainSpec:
    """Build one registry row; discovery = per-chain verified sources."""
    sources = ("geckoterminal", "rmi_indexed_pairs")
    slug = GT_NETWORKS.get(name)
    described = (
        f"geckoterminal(api/v2/networks/{slug}/trending_pools)"
        if slug else f"geckoterminal(unwired for {name})"
    ) + "+rmi_indexed_pairs(/api/v1/market/new-pairs)"
    return ChainSpec(name, aliases, native, described, quote, DEXSCREENER, sources)


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

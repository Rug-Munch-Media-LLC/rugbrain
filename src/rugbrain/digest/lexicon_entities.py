"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Named-entity word lists — protocols, chains, and the tickers that alias them.

Matched on a word boundary, case-insensitive. A miss costs recall on a
graph edge; a false hit costs precision. The list is long on purpose for
the generic-impact names and short on the named entities, where each
entry is a real node in its own right.

``CHAIN_TICKER_SYMBOLS`` exists only to collapse "$SOL is Solana" into one
graph node. Only symbols a chain actually trades under are listed — $SOLANA
and $BASE are ordinary tokens and must survive, because a scammer naming a
token "$BASE" is exactly the case this extractor exists to catch.
"""

from __future__ import annotations

from typing import Final

PROTOCOLS: Final = (
    "uniswap",
    "aave",
    "curve",
    "compound",
    "makerdao",
    "chainlink",
    "sushiswap",
    "pancakeswap",
    "euler",
    "marinade",
    "jupiter",
    "raydium",
    "orca",
    "pyth",
    "axelar",
    "layerzero",
    "stargate",
    "wormhole",
    "hyperliquid",
    "gmx",
    "arbitrum",
    "optimism",
    "polygon",
    "avalanche",
    "solana",
    "ethereum",
    "bitcoin",
    "tron",
    "monero",
    "cbridge",
    "debridge",
    "across",
    "starknet",
    "celestia",
)

CHAINS: Final = (
    "ethereum",
    "solana",
    "bitcoin",
    "binance",
    "arbitrum",
    "base",
    "polygon",
    "optimism",
    "avalanche",
    "tron",
    "bsc",
    "sui",
    "aptos",
    "cosmos",
    "near",
    "stellar",
    "cardano",
    "dogecoin",
    "monero",
)

CHAIN_TICKER_SYMBOLS: Final = frozenset(
    {
        "SOL",
        "ETH",
        "BTC",
        "BNB",
        "TRX",
        "POL",
        "AVAX",
        "ARB",
        "OP",
        "DOGE",
        "XMR",
        "SUI",
        "APT",
        "NEAR",
        "ATOM",
    }
)

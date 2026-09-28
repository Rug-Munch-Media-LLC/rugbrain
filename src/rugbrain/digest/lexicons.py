"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The word lists extraction matches against. Data, not logic.

Each lexicon is deliberately blunt and deliberately sized for its role:

* **severity** drives which Qdrant collection a document lands in, so a
  false "critical" costs more than a false "low" — the words are
  aggressive.
* **protocols / chains** cost recall on a graph edge when they miss and
  precision when they false-positive, so the generic-impact groups are
  long and the named-entity groups are short.
* **ticker symbols** exist only to collapse "$SOL is Solana" into one
  graph node. Only symbols a chain actually trades under are listed —
  $SOLANA and $BASE are ordinary tokens and must survive, because a
  scammer naming a token "$BASE" is exactly the case we exist to catch.

The patterns are ordered or first-match-wins; see ``severity.py``.
"""

from __future__ import annotations

import re
from typing import Final

# Ordered: the first hit wins, so put the most specific first.
SEVERITY_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "critical",
        (
            "rug pull",
            "rugpull",
            "rug pulled",
            "drainer",
            "drainers",
            "honeypot",
            "private key compromised",
            "private key leak",
            "exploit",
            "exploited",
            "drained",
            "drain ",
            "stolen funds",
            "compromised",
            "breach",
            "exit scam",
            "insider",
        ),
    ),
    (
        "high",
        (
            "phishing",
            "scam",
            "scammed",
            "laundering",
            "sanction",
            "arrest",
            "seized",
            "impersonat",
            "blackmail",
        ),
    ),
    (
        "medium",
        (
            "vulnerability",
            "vuln",
            "attack",
            "suspected",
            "warning",
            "malicious",
            "outage",
            "freeze",
        ),
    ),
)

# Protocol / chain names, matched on a word boundary, case-insensitive.
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

# A ticker symbol a chain itself trades under — see the module docstring.
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

# Bare protocol references in prose ("the Euler v2 exploit").
NAME_RE: Final = re.compile(r"(?<![\w-])([a-z][a-z0-9-]{2,20})(?![\w-])", re.IGNORECASE)

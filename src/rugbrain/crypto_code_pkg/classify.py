"""Query classification for the crypto-code path.

One job: decide whether a question is about crypto CODE (contract languages,
exploit vocabulary, testing tools) or is a general RAG query (prices, wallets,
news, scam checks). A lightweight keyword classifier, deliberately — no model
dependency, and it must never be the reason a search fails.

Split out of ``rugbrain.crypto_code_retrieval`` so the vocabulary can be read
and tested without importing the retriever or its Qdrant client.
"""

from __future__ import annotations

import re

# Lightweight keyword classifier — no new model dependency. Word-boundary
# regexes over contract languages, exploit vocabulary, and testing tools.
_CRYPTO_CODE_TERM_RE = re.compile(
    r"\b("
    r"solidity|solana|rust|move|vyper|cairo|anchor|foundry|hardhat|"
    r"reentrancy|honeypot|rugpull|rug\s?pull|flash\s?loan|oracle\s?manipulation|"
    r"sandwich|frontrunning|mev|slippage|overflow|underflow|"
    r"erc-?20|erc-?721|erc-?1155|spl|pda|cpi|idl|abi|"
    r"uniswap|aave|compound|curve|jupiter|raydium|"
    r"forge\s?test|cargo\s?test|pytest|slither|mythril|echidna|"
    r"smart\s?contract|deployer|mint\s?function|hidden\s?mint|"
    r"tx\.origin|selfdestruct|delegatecall"
    r")\b",
    re.IGNORECASE,
)

_CRYPTO_CODE_HINTS = (
    "how does the contract",
    "how does this contract",
    "contract pattern",
    "exploit code",
    "poc for",
    "proof of concept",
    "audit finding",
    "security pattern",
    "test the contract",
    "write a test for",
)


def classify_crypto_code_query(query: str) -> bool:
    """Return True when *query* looks like a crypto-CODE question.

    General RAG queries (prices, wallets, news, scam checks) return False and
    keep the existing path.
    """
    q = (query or "").strip().lower()
    if not q:
        return False
    if any(hint in q for hint in _CRYPTO_CODE_HINTS):
        return True
    return bool(_CRYPTO_CODE_TERM_RE.search(q))

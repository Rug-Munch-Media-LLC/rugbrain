"""Vector / chain classification helpers.

Mirrors ``rugbrain.crypto_schema`` without a hard import so the alert
package stays importable in minimal environments.
"""

from __future__ import annotations

import re

_VECTOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"re-?entr(anc)?y", re.I), "reentrancy"),
    (re.compile(r"flash ?loan", re.I), "flash-loan"),
    (re.compile(r"oracle|price ?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access ?control|delegatecall|tx\.origin", re.I), "access-control"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"private ?key|seed phrase|key compromise", re.I), "key-compromise"),
    (re.compile(r"rug ?pull|liquidity removed|exit scam|pump", re.I), "rug-pull"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"withdraw", re.I), "withdrawal-exploit"),
    (re.compile(r"denial of service|dos", re.I), "denial-of-service"),
    (re.compile(r"front.?run|sandwich|mev", re.I), "front-running"),
    (re.compile(r"supply chain", re.I), "supply-chain"),
    (re.compile(r"input valid", re.I), "input-validation"),
    (re.compile(r"exploit|hack|vulnerab|bug", re.I), "smart-contract-exploit"),
]

_CHAIN_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("ethereum", ("ethereum", "eth ", "erc-20", "erc-721", "evm", "mainnet")),
    ("bsc", ("bsc", "binance smart chain", "bnb chain", "bep-20")),
    ("solana", ("solana", "spl")),
    ("polygon", ("polygon", "matic")),
    ("arbitrum", ("arbitrum", "arb ")),
    ("optimism", ("optimism", "op mainnet")),
    ("base", ("base",)),
    ("avalanche", ("avalanche", "avax")),
    ("tron", ("tron", "trc-20")),
    ("fantom", ("fantom", "ftm")),
    ("multi", ("multi-chain", "cross-chain", "multiple chains")),
]


def detect_vector(text: str) -> str:
    lowered = text or ""
    for pattern, vector in _VECTOR_PATTERNS:
        if pattern.search(lowered):
            return vector
    return "unknown"


def detect_chain(text: str) -> str:
    lowered = f" {text.lower()} "
    found: set[str] = set()
    for chain, aliases in _CHAIN_ALIASES:
        for alias in aliases:
            if alias in lowered:
                found.add(chain)
                break
    if re.search(r"\b0x[a-fA-F0-9]{8,}\b", lowered):
        found.add("ethereum")
    if "multi" in found:
        return "multi"
    if len(found) > 1:
        return "multi"
    return found.pop() if found else "unknown"

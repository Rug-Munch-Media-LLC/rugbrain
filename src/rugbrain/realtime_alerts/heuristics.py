"""Exploit heuristics: text classification, USD amount and chain detection."""

from __future__ import annotations

import re

_EXPLOIT_RE = re.compile(
    r"exploit|hack|drain|stolen|stole|steal|compromis|rug\b|vulnerab|attack|"
    r"breach|phish|key leak|private key|approved|swept|drained",
    re.I,
)
_AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s?([mMkKbB])?", re.I)
_AMOUNT_MULT = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9, "K": 1e3, "M": 1e6, "B": 1e9}
_CHAIN_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("ethereum", ("ethereum", "eth", "erc-20", "erc-721", "mainnet")),
    ("bsc", ("bsc", "binance", "bnb chain", "bep-20")),
    ("solana", ("solana", "spl")),
    ("polygon", ("polygon", "matic")),
    ("arbitrum", ("arbitrum", "arb")),
    ("optimism", ("optimism", "op mainnet")),
    ("base", ("base",)),
    ("avalanche", ("avalanche", "avax")),
    ("tron", ("tron", "trc-20")),
    ("fantom", ("fantom", "ftm")),
    ("multi", ("multi-chain", "cross-chain", "multiple chains")),
]

_CRITICAL_KEYWORDS = (
    "exploit",
    "drain",
    "stolen",
    "compromis",
    "critical",
    "urgent",
    "vulnerab",
    "attack",
)


def is_exploit_candidate(text: str) -> bool:
    """Heuristic: does *text* look like an exploit/hack report?"""
    return bool(_EXPLOIT_RE.search(text or ""))


def parse_amount_usd(text: str) -> float:
    """Best-effort ``$1.2M`` / ``$4,800,000`` / ``$300k`` extraction."""
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    mult = _AMOUNT_MULT.get(m.group(2) or "", 1.0)
    return value * mult


def detect_chain_critical(text: str) -> str:
    """Return the first chain alias found in *text*, else ``"unknown"``."""
    lowered = (text or "").lower()
    for chain, aliases in _CHAIN_ALIASES:
        for alias in aliases:
            if alias in lowered:
                return chain
    return "unknown"

"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Recognising entity *shapes* in text — addresses, domains, tickers.

An address is a *shape*, not a meaning. Every pattern here is exact and
free, where an LLM can hallucinate an address that never existed — and a
false address in a crime RAG is worse than no address at all. Extraction
also runs on every ingested document, so it must never be a network call.

EVM addresses are checksum-shaped (EIP-55) so a truncated or mistyped
address from a bad scrape is dropped instead of becoming a phantom
entity. Solana base58 is length- and charset-bounded. TON/base58-34 is
ambiguous with Solana and is therefore matched as TRON only.
"""

from __future__ import annotations

import re
from typing import Final

EVM_RE: Final = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Solana: base58, no 0/O/I/l, 32-44 chars. Requires a word boundary that is
# not itself a base58 char so we do not clip a longer run mid-address.
_B58 = r"[1-9A-HJ-NP-Za-km-z]"
SOL_RE: Final = re.compile(rf"\b({_B58}{{32,44}})\b")

# TON / base58 34 chars is ambiguous with Solana; excluded deliberately.
TRON_RE: Final = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")

# Domain (phishing / drainer infrastructure).
DOMAIN_RE: Final = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|io|xyz|info|app|dev|ru|cn|top|live|site|vip|cc)\b",
    re.IGNORECASE,
)

# Tokens quoted as $TICKER. The (?<![\w$]) lookbehind avoids matching "$5"
# or "$1,000" — the digits-then-letter boundary is what makes this precise
# enough to use as a graph node identity.
TICKER_RE: Final = re.compile(r"(?<![\w$])\$([A-Z][A-Z0-9]{1,9})\b")


def evm_checksum(address: str) -> bool:
    """True when *address* is a valid EIP-55 mixed-case-checksum address.

    An all-lowercase or all-uppercase address is *syntactically* valid
    (0x + 40 hex) but carries no checksum, so it is more likely a
    hand-typed or OCR'd value than a copied one. We still accept it as an
    address; this function is used by the caller to decide whether to
    trust it as an identity, not whether it is shaped like one.
    """
    if not EVM_RE.fullmatch(address):
        return False
    body = address[2:]
    return not (body.islower() or body.isupper())


def solana_candidates(text: str) -> set[str]:
    """Base58 runs of Solana-plausible length, minus known false positives.

    The base58 shape is very permissive: an ordinary 40-char word or a
    git SHA also matches. We require a mixed case (Solana addresses are
    not all-lowercase) and a digit, which kills the overwhelming majority
    of prose and hex-SHA false positives.
    """
    out: set[str] = set()
    for cand in SOL_RE.findall(text):
        if len(cand) < 32:
            continue
        if not any(c.isdigit() for c in cand):
            continue
        if cand.islower() or cand.isupper():
            continue
        out.add(cand)
    return out

"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Address extraction from video text.

Extraction is a LEAD, not proof: a mint found in a title is a reason to query
chain data, never a finding on its own. The Solana heuristic deliberately
rejects all-letter candidates so ordinary prose cannot become an address; the
cost is low recall for the rare digit-free mint, which the on-chain step would
have to confirm anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Chain = Literal["evm", "solana"]

# 40 hex chars, not part of a longer hex run (a 64-char tx hash must not match).
_EVM_RE = re.compile(r"(?<![0-9A-Fa-fx])0x[0-9A-Fa-f]{40}(?![0-9A-Fa-f])")

# Base58 alphabet, bounded so it cannot be a slice of a longer base58 run.
_B58 = "1-9A-HJ-NP-Za-km-z"
_SOL_RE = re.compile(rf"(?<![{_B58}])[{_B58}]{{32,44}}(?![{_B58}])")

# Burn/sentinel addresses carry no intelligence.
_IGNORED_EVM = frozenset(
    {
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
    }
)


@dataclass(frozen=True, slots=True)
class AddressHit:
    """One candidate address plus where it appeared."""

    address: str
    chain: Chain
    field: str


def _solana_candidate(candidate: str) -> bool:
    if candidate.isalpha():  # prose, not an address
        return False
    if len(set(candidate)) < 8:  # '111111...' padding, not a mint
        return False
    digits = sum(char.isdigit() for char in candidate)
    uppercase = sum(char.isupper() for char in candidate)
    return digits >= 1 or uppercase >= 3


def extract_addresses(text: str, field: str = "text") -> tuple[AddressHit, ...]:
    """Return de-duplicated EVM and Solana candidates found in ``text``."""
    hits: list[AddressHit] = []
    seen: set[str] = set()

    for match in _EVM_RE.finditer(text):
        address = match.group(0).lower()
        if address in _IGNORED_EVM or address in seen:
            continue
        seen.add(address)
        hits.append(AddressHit(address=address, chain="evm", field=field))

    for match in _SOL_RE.finditer(text):
        candidate = match.group(0)
        if candidate in seen or not _solana_candidate(candidate):
            continue
        seen.add(candidate)
        hits.append(AddressHit(address=candidate, chain="solana", field=field))

    return tuple(hits)

"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Run every shape recogniser over a document and assemble the EntitySet.

Pure, dependency-free, deterministic. Extraction runs on the ingest hot
path, so it is regex-only and offline by design (see the module notes in
``entity_shapes.py`` for why a model would be worse here).

The same text in must produce the same entities out, or a re-ingest
duplicates graph nodes. Everything below is order-independent and
set-based to keep that guarantee.
"""

from __future__ import annotations

import re

from rugbrain.digest.entity_shapes import (
    DOMAIN_RE,
    EVM_RE,
    TICKER_RE,
    TRON_RE,
    evm_checksum,
    solana_candidates,
)
from rugbrain.digest.entity_types import EntitySet
from rugbrain.digest.lexicons import CHAIN_TICKER_SYMBOLS, CHAINS, PROTOCOLS
from rugbrain.digest.severity import classify_severity


def _dedupe(seq: list[str]) -> tuple[str, ...]:
    """Order-preserving dedupe (first occurrence wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _named_matches(names: tuple[str, ...], lowered: str) -> tuple[str, ...]:
    """Match lexicon names on a word boundary, not as a bare substring.

    "base" must not match inside "database", and "sol" must not match
    "console"; the boundary is what keeps the node set clean.
    """
    return _dedupe([n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", lowered)])


def extract(text: str, *, max_chars: int = 200_000) -> EntitySet:
    """Extract every crypto-relevant entity from *text*.

    Truncates to *max_chars* defensively: a 10 MB paste would otherwise
    cost a lot of regex time for no additional entities, and the caller
    stores chunks anyway.
    """
    if not text:
        return EntitySet()
    body = text[:max_chars]

    severity, matched = classify_severity(body)
    evm = {a for a in EVM_RE.findall(body) if len(a) == 42}
    tron = set(TRON_RE.findall(body))
    # A T-address is also a valid base58 string; drop it from the Solana
    # set so one address never becomes two graph nodes.
    sol = solana_candidates(body) - tron

    lowered = body.lower()
    # A $TICKER that is a chain's own ticker symbol is one identity, not
    # two — $SOL is the chain Solana, so a graph node keyed on both would
    # split one subject in half. Only symbols the chain itself actually
    # trades under are removed; see CHAIN_TICKER_SYMBOLS.
    tickers = frozenset(t for t in TICKER_RE.findall(body) if t not in CHAIN_TICKER_SYMBOLS)

    return EntitySet(
        evm_addresses=frozenset(a for a in evm if evm_checksum(a) or a[2:].islower()),
        solana_addresses=frozenset(sol),
        tron_addresses=frozenset(tron),
        domains=frozenset(d.lower() for d in DOMAIN_RE.findall(body)),
        tickers=tickers,
        protocols=_named_matches(PROTOCOLS, lowered),
        chains=_named_matches(CHAINS, lowered),
        severity=severity,
        matched_rules=matched,
    )

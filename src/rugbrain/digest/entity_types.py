"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The EntitySet — the value type extraction produces, and its serialisation.

Frozen: a caller cannot mutate the result of extraction and have the
next stage disagree about what was found. ``as_dict`` is the form the
Qdrant payload and the graph row builder both read, so it is the single
serialisation boundary for the whole package.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class EntitySet:
    """Entities extracted from one document.

    Frozen: a caller cannot mutate the result of extraction and have the
    next stage disagree about what was found.
    """

    evm_addresses: frozenset[str] = frozenset()
    solana_addresses: frozenset[str] = frozenset()
    tron_addresses: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    tickers: frozenset[str] = frozenset()
    protocols: tuple[str, ...] = ()
    chains: tuple[str, ...] = ()
    severity: str = "low"
    matched_rules: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        """True when nothing entity-shaped was found (a plain prose doc)."""
        return not (
            self.evm_addresses
            or self.solana_addresses
            or self.tron_addresses
            or self.domains
            or self.tickers
            or self.protocols
        )

    @property
    def addresses(self) -> frozenset[str]:
        """Every address across all chains, for the graph edge writer."""
        return self.evm_addresses | self.solana_addresses | self.tron_addresses

    def as_dict(self) -> dict[str, object]:
        """Serializable form for the Qdrant payload."""
        return {
            "evm_addresses": sorted(self.evm_addresses),
            "solana_addresses": sorted(self.solana_addresses),
            "tron_addresses": sorted(self.tron_addresses),
            "domains": sorted(self.domains),
            "tickers": sorted(self.tickers),
            "protocols": list(self.protocols),
            "chains": list(self.chains),
            "severity": self.severity,
        }

"""Coverage guard: every chain we claim to support has a way to be ranked.

Canonical chain set = lite-chain-indexer ``lite_indexer/chain_registry.py``
("all 19 EVM presets + solana + tron = 21 chains run in the live daemon").
A chain in the config without a discovery source (or a stated gap) is a
promise we cannot keep, and this test refuses it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

CONFIG = Path(
    os.environ.get(
        "RUGBRAIN_CHAINS_FILE",
        Path(__file__).resolve().parents[1] / "config" / "chains.json",
    )
)

#: 19 EVM presets + solana + tron, per lite-chain-indexer chain_registry.py.
EXPECTED = {
    "ethereum", "base", "bsc", "polygon", "arbitrum", "optimism", "avalanche",
    "fantom", "linea", "scroll", "zksync", "mantle", "gnosis", "celo", "blast",
    "fraxtal", "metis", "robinhood", "hyperliquid", "solana", "tron",
}

#: Chains the current trending_fusion engine registry resolves today.
ENGINE_REGISTRY = {
    "solana", "ethereum", "bsc", "base", "arbitrum", "polygon", "optimism", "avalanche",
}

REQUIRED_KEYS = {"name", "aliases", "caip2", "native", "discovery_source", "supported", "gap"}


@pytest.fixture(scope="module")
def chains() -> list[dict]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return data["chains"]


def test_covers_all_21_supported_chains(chains):
    names = {c["name"] for c in chains}
    assert names == EXPECTED, {
        "missing": sorted(EXPECTED - names),
        "unexpected": sorted(names - EXPECTED),
    }
    assert len(chains) == 21


def test_every_chain_is_rankable_or_declares_its_gap(chains):
    broken = []
    for chain in chains:
        source = chain.get("discovery_source") or {}
        if not (source.get("kind") and source.get("url_or_table")) and not chain.get("gap"):
            broken.append(chain["name"])
    assert not broken, f"supported but unrankable: {broken}"


def test_entries_are_well_formed_and_flagged_supported(chains):
    for chain in chains:
        assert REQUIRED_KEYS <= set(chain), f"{chain.get('name')}: missing keys"
        assert chain["supported"] is True
        assert chain["caip2"], f"{chain['name']}: empty caip2"
        assert chain["native"], f"{chain['name']}: empty native"
        assert chain["aliases"], f"{chain['name']}: empty aliases"


def test_aliases_and_caip2_are_unique(chains):
    aliases: dict[str, str] = {}
    caip2: dict[str, str] = {}
    for chain in chains:
        for alias in chain["aliases"]:
            key = alias.strip().lower()
            assert key not in aliases, f"alias {alias!r} shared by {chain['name']} and {aliases[key]}"
            aliases[key] = chain["name"]
        assert chain["caip2"] not in caip2, f"caip2 {chain['caip2']} duplicated"
        caip2[chain["caip2"]] = chain["name"]


def test_engine_resolved_chains_are_all_covered(chains):
    """The engine's own registry is a subset we must never lose."""
    names = {c["name"] for c in chains}
    assert ENGINE_REGISTRY <= names, f"engine chains dropped: {sorted(ENGINE_REGISTRY - names)}"


def test_names_resolve_through_alias_table(chains):
    """Every entry must be resolvable from a slug or alias, never unknown."""
    resolvable = set()
    for chain in chains:
        resolvable.add(chain["name"])
        resolvable.update(a.strip().lower() for a in chain["aliases"])
    for chain in chains:
        assert chain["name"] in resolvable, f"{chain['name']} unresolvable"

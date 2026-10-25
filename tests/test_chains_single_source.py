"""Single-source-of-truth guards for the 21-chain trending registry.

config/chains.json is the only place a chain, alias or GeckoTerminal slug may
live. These tests fail if code re-introduces a hardcoded registry, ranks an
unverified chain silently, or grows a second scorer.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from rugbrain.trending_fusion import chains as registry
from rugbrain.trending_fusion.discovery import gather_rows, sources_for

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "rugbrain" / "trending_fusion"


def _config() -> dict:
    return json.loads((ROOT / "config" / "chains.json").read_text(encoding="utf-8"))


def _names_and_aliases() -> set[str]:
    out: set[str] = set()
    for chain in _config()["chains"]:
        out.add(chain["name"].lower())
        out.update(a.lower() for a in chain.get("aliases") or ())
    return out


def test_chains_module_contains_no_chain_literal():
    """A chain name or alias hardcoded in chains.py is a second source of truth."""
    source = (PKG / "chains.py").read_text(encoding="utf-8")
    literals = {lit.lower() for lit in re.findall(r"""["']([A-Za-z0-9_-]{2,40})["']""", source)}
    leaked = literals & _names_and_aliases()
    assert not leaked, f"chain literals hardcoded in chains.py: {sorted(leaked)}"


def test_all_21_chains_resolve_by_name_and_every_alias():
    assert len(_config()["chains"]) == 21
    for chain in _config()["chains"]:
        spec = registry.require_chain(chain["name"])
        assert spec.name == chain["name"]
        assert spec.caip2 and spec.native_token
        for alias in chain.get("aliases") or ():
            assert registry.require_chain(alias).name == chain["name"]


def test_unknown_chain_is_a_typed_refusal_naming_supported_chains():
    with pytest.raises(registry.UnknownChain) as excinfo:
        registry.require_chain("dogechain")
    message = str(excinfo.value)
    assert excinfo.value.chain == "dogechain"
    assert len(excinfo.value.supported) == 21
    for name in ("ethereum", "solana", "tron", "hyperliquid"):
        assert name in message
    assert registry.resolve_chain("dogechain") is None


def test_null_verified_at_reads_as_unverified_never_ranked():
    for chain in _config()["chains"]:
        if (chain.get("discovery_source") or {}).get("verified_at") is None:
            spec = registry.require_chain(chain["name"])
            assert spec.verified is False, f"{spec.name}: null verified_at must be unverified"


def test_rate_limited_source_degrades_naming_itself():
    async def limited(chain: str):
        raise RuntimeError("429 Too Many Requests")

    async def ok(chain: str):
        return [
            {
                "source": "rmi_indexed_pairs",
                "chain": chain,
                "token_address": "0xabc",
                "symbol": "ABC",
                "signals": {"volume_24h": 10.0},
            }
        ]

    out = asyncio.run(
        gather_rows("solana", sources=(("geckoterminal", limited), ("rmi_indexed_pairs", ok)))
    )
    assert "429" in out["missing"]["geckoterminal"]
    assert out["sources_ok"] == ["rmi_indexed_pairs"]
    assert len(out["rows"]) == 1


def test_own_index_only_for_fantom_and_tron():
    for name in ("fantom", "tron"):
        spec = registry.require_chain(name)
        assert spec.source_kind == "own_index"
        assert spec.gt_slug is None
        assert [n for n, _ in sources_for(name)] == ["rmi_indexed_pairs"]
    assert registry.require_chain("fantom").verified_at == "2026-10-25"


def test_tron_declares_its_gap_fantom_does_not():
    tron = registry.require_chain("tron")
    assert tron.gap and "0 rows" in tron.gap and "tron" in tron.gap
    assert tron.verified is False
    fantom = registry.require_chain("fantom")
    assert fantom.gap is None and fantom.verified is True


def test_there_is_exactly_one_scorer():
    """fuse() lives in fuse.py; trending_fused() in sources.py. No second scorer."""
    definers = {
        path.name
        for path in PKG.glob("*.py")
        for text in [path.read_text(encoding="utf-8")]
        if re.search(r"^def fuse\(", text, re.M)
        or re.search(r"^async def trending_fused\(", text, re.M)
    }
    assert definers == {"fuse.py", "sources.py"}

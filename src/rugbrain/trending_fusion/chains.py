"""Chain registry for the one trending engine -- config is the ONLY source.

``config/chains.json`` (21 canonical chains, provenance: lite-chain-indexer's
live daemon registry) is loaded here and nowhere else. No chain name, alias or
GeckoTerminal slug is hardcoded: adding, demoting or re-verifying a chain is a
config edit. ``tests/test_chains_single_source.py`` reads this file and fails on
any chain literal.

``verified_at: null`` means UNVERIFIED -- never "supported and ranked". Callers
must surface it (``missing_sources['chain_unverified']``), not drop it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "chains.json"
GT_MARKER = "/networks/"


def config_path() -> Path:
    """Resolve the registry file (env override, repo checkout, then cwd)."""
    override = os.environ.get("RUGBRAIN_CHAINS_FILE")
    if override:
        return Path(override)
    if DEFAULT_CONFIG.exists():
        return DEFAULT_CONFIG
    return Path.cwd() / "config" / "chains.json"


class UnknownChain(ValueError):
    """Typed refusal: the requested chain is not in the canonical registry."""

    def __init__(self, chain: str, supported: tuple[str, ...]) -> None:
        self.chain = chain
        self.supported = supported
        super().__init__(
            f"unsupported chain {chain!r}: supported chains ({len(supported)}) are "
            f"{', '.join(supported)} -- add it to config/chains.json, never to code"
        )


@dataclass(frozen=True)
class ChainSpec:
    """One registry row, exactly as the config states it."""

    name: str
    aliases: tuple[str, ...]
    caip2: str
    native_token: str
    source_kind: str
    source_ref: str
    verified_at: str | None
    gap: str | None
    supported: bool = True

    @property
    def verified(self) -> bool:
        """Verified only with a dated live probe and no declared gap."""
        return bool(self.verified_at) and not self.gap

    @property
    def gt_slug(self) -> str | None:
        """GeckoTerminal slug parsed from the config URL -- never a code literal."""
        if self.source_kind != "geckoterminal" or GT_MARKER not in self.source_ref:
            return None
        slug = self.source_ref.split(GT_MARKER, 1)[1].split("/", 1)[0].strip()
        return slug or None

    @property
    def adapter_names(self) -> tuple[str, ...]:
        """Discovery adapters this chain is allowed to use."""
        if self.gt_slug:
            return ("geckoterminal", "rmi_indexed_pairs")
        return ("rmi_indexed_pairs",)


@lru_cache(maxsize=4)
def _load(path_str: str) -> tuple[ChainSpec, ...]:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    specs = []
    for row in data.get("chains") or []:
        source = row.get("discovery_source") or {}
        specs.append(
            ChainSpec(
                name=str(row["name"]).strip().lower(),
                aliases=tuple(str(a).strip().lower() for a in row.get("aliases") or ()),
                caip2=str(row.get("caip2") or ""),
                native_token=str(row.get("native") or ""),
                source_kind=str(source.get("kind") or ""),
                source_ref=str(source.get("url_or_table") or ""),
                verified_at=source.get("verified_at"),
                gap=row.get("gap"),
                supported=bool(row.get("supported", True)),
            )
        )
    return tuple(specs)


def load_chains(path: Path | str | None = None) -> tuple[ChainSpec, ...]:
    """Every registry row, in config order."""
    return _load(str(path or config_path()))


def _index() -> dict[str, ChainSpec]:
    index: dict[str, ChainSpec] = {}
    for spec in load_chains():
        index[spec.name] = spec
        for alias in spec.aliases:
            index.setdefault(alias, spec)
    return index


def resolve_chain(name: str) -> ChainSpec | None:
    """Resolve a canonical name or alias; ``None`` when unsupported."""
    return _index().get((name or "").strip().lower())


def require_chain(name: str) -> ChainSpec:
    """Resolve or raise :class:`UnknownChain` naming every supported chain."""
    spec = resolve_chain(name)
    if spec is None:
        raise UnknownChain((name or "").strip(), tuple(s.name for s in load_chains()))
    return spec


def gt_networks() -> dict[str, str]:
    """Chain name -> GeckoTerminal slug, derived from the config."""
    return {s.name: s.gt_slug for s in load_chains() if s.gt_slug}

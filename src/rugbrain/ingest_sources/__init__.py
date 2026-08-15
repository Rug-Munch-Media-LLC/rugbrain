"""Programmatic top-tier crypto data sources for RugBrain ingestion.

Each source implements the ``Source.fetch() -> list[IngestDoc]`` interface
(see ``base.py``) and normalizes upstream GitHub/API content into
``IngestDoc`` records carrying a semantic ``doc_type``:

  * ``defihacklabs``           — real DeFi exploit PoCs      (exploit_poc)
  * ``swc_registry``           — SWC weakness taxonomy       (vulnerability_pattern)
  * ``eips`` / ``bips``        — protocol standards          (standard)
  * ``openzeppelin_contracts`` — audited Solidity code       (contract_code)
  * ``rekt_news``              — post-mortem articles        (post_mortem)
  * ``crypto_whitepapers``     — project whitepapers         (whitepaper)
  * ``defillama``              — live TVL/market context     (market_data)
  * ``layerzero`` / ``axelar`` / ``ccip`` — bridge contracts (bridge_architecture)
  * ``l2beat``                 — L2/bridge risk framework    (l2_risk)
  * ``circom`` / ``snarkjs``   — ZK toolchain docs           (zk_circuit)
  * ``mev_boost`` / ``mev_boost_relay`` — PBS/relay docs     (mev_architecture)
  * ``safe_contracts``         — Safe multisig Solidity      (contract_code)
  * ``tornado_cash``           — zk-mixer reference code     (privacy_protocol)
  * ``blacklists``             — Web3 phishing blacklists    (scam_entity)

Fetching is deliberately scaffolding-grade: sources do one tree-listing call
plus per-file raw fetches (bounded by ``limit``), never multi-GB clones.
Chunking/embedding/storage stay in the existing ingest pipelines — use
``to_crawl_document`` to bridge into ``ingest_common.store_documents``.

Usage::

    python -m rugbrain.ingest_sources --source swc_registry --limit 3
    python -m rugbrain.ingest_sources --list
"""

from __future__ import annotations

import asyncio
import logging

from rugbrain.ingest_sources.base import IngestDoc, Source, to_crawl_document
from rugbrain.ingest_sources.blacklists import BlacklistsSource
from rugbrain.ingest_sources.bridges import AxelarSource, CCIPSource, LayerZeroSource
from rugbrain.ingest_sources.defihacklabs import DeFiHackLabsSource
from rugbrain.ingest_sources.defillama import DeFiLlamaSource
from rugbrain.ingest_sources.eips import BIPsSource, EIPsSource
from rugbrain.ingest_sources.incidents import (
    BlockSecDefiPocSource,
    GutteDeFiSource,
    ImmunefiHackAnalysesSource,
    SlowMistOpenReportsSource,
)
from rugbrain.ingest_sources.l2beat import L2BeatSource
from rugbrain.ingest_sources.mev import MEVBoostRelaySource, MEVBoostSource
from rugbrain.ingest_sources.openzeppelin import OpenZeppelinSource
from rugbrain.ingest_sources.privacy import TornadoSource
from rugbrain.ingest_sources.rekt import RektSource
from rugbrain.ingest_sources.safe import SafeSource
from rugbrain.ingest_sources.swc import SWCSource
from rugbrain.ingest_sources.whitepapers import WhitepapersSource
from rugbrain.ingest_sources.zk import CircomSource, SnarkJSSource

log = logging.getLogger(__name__)

# Registry — all source instances. EIPs and BIPs are separate sources so each
# can be fetched/limited independently.
SOURCES: tuple[Source, ...] = (
    DeFiHackLabsSource(),
    SWCSource(),
    EIPsSource(),
    BIPsSource(),
    OpenZeppelinSource(),
    RektSource(),
    WhitepapersSource(),
    DeFiLlamaSource(),
    LayerZeroSource(),
    AxelarSource(),
    CCIPSource(),
    L2BeatSource(),
    CircomSource(),
    SnarkJSSource(),
    MEVBoostSource(),
    MEVBoostRelaySource(),
    SafeSource(),
    TornadoSource(),
    BlacklistsSource(),
    GutteDeFiSource(),
    ImmunefiHackAnalysesSource(),
    BlockSecDefiPocSource(),
    SlowMistOpenReportsSource(),
)

# Semantic doc_type → source names (for quick lookup / filtering).
DOC_TYPES: dict[str, tuple[str, ...]] = {
    dt: tuple(s.name for s in SOURCES if s.doc_type == dt) for dt in {s.doc_type for s in SOURCES}
}


def get_source(name: str) -> Source | None:
    """Return the source named *name*, or None."""
    for src in SOURCES:
        if src.name == name:
            return src
    return None


def list_sources() -> list[str]:
    """All registered source names."""
    return [s.name for s in SOURCES]


async def fetch_source(name: str, limit: int | None = None) -> list[IngestDoc]:
    """Fetch *name* via its Source; [] when unknown or on failure."""
    src = get_source(name)
    if src is None:
        log.warning("unknown_source name=%s available=%s", name, list_sources())
        return []
    return await src.fetch(limit=limit)


async def fetch_all(limit: int | None = None) -> dict[str, list[IngestDoc]]:
    """Fetch every source; returns ``{source_name: [IngestDoc, ...]}``."""
    out: dict[str, list[IngestDoc]] = {}
    for src in SOURCES:
        out[src.name] = await src.fetch(limit=limit)
        log.info("source_done name=%s docs=%d", src.name, len(out[src.name]))
    return out


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RugBrain programmatic data sources")
    parser.add_argument("--list", action="store_true", help="list sources + doc_types")
    parser.add_argument("--source", type=str, default=None, help="fetch one source by name")
    parser.add_argument("--limit", type=int, default=None, help="max docs per source")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if args.list:
        for s in SOURCES:
            print(f"{s.name:<26} {s.doc_type:<22} {s.description}")
        return
    if args.source:
        src = get_source(args.source)
        if src is None:
            print(f"unknown source: {args.source}; available: {list_sources()}")
            return
        docs = asyncio.run(src.fetch(limit=args.limit))
        print(f"{src.name}: fetched {len(docs)} docs (doc_type={src.doc_type})")
        for d in docs[:5]:
            print(f"  - {d.title or d.doc_id} | {d.url}")
        return
    parser.print_help()


if __name__ == "__main__":
    _main()


__all__ = [
    "DOC_TYPES",
    "SOURCES",
    "IngestDoc",
    "Source",
    "fetch_all",
    "fetch_source",
    "get_source",
    "list_sources",
    "to_crawl_document",
]

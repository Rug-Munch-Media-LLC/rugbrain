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
  * ``chainabuse``             — live crowdsourced scam reports (crime_report)
  * ``crypto_rekts``           — rekt.news incident mirror   (post_mortem)
  * ``phishdestroy_destroylist`` / ``openphish_feed`` / ``urlhaus_feed`` /
    ``blocksecteam_phishing_contracts`` — real-time phishing/drainer intel (scam_entity)
  * ``deflabx_solrpds`` / ``yaudit_defi_fork_bugs`` /
    ``coinspect_learn_evm_attacks`` — Solana rug-pull + fork-bug + EVM exploit corpora
  * ``seal_911``               — SEAL 911 whitehat response log (incident)
  * ``trailofbits_publications`` / ``gcf3711_audit_report_archive_hf`` —
    audit-findings corpora (audit_finding)
  * ``emperor_mew_cftc_actions`` / ``cftc_press_releases_rss`` /
    ``opensanctions`` — regulation/enforcement + sanctions wallets
  * ``samczsun_blog`` + ``trailofbits_blog_rss`` / ``zellic_blog_rss`` /
    ``immunefi_blog_rss`` / ``neodyme_blog_rss`` — research corpora (research)
  * ``ossf_malicious_packages`` / ``cisa_kev`` — vulnerability registries
    (vulnerability_pattern)

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
from rugbrain.ingest_sources.blocksecteam_phishing import BlockSecPhishingContractsSource
from rugbrain.ingest_sources.bridges import AxelarSource, CCIPSource, LayerZeroSource
from rugbrain.ingest_sources.chainabuse import ChainabuseSource
from rugbrain.ingest_sources.cisa_kev import CISAKevSource
from rugbrain.ingest_sources.coinspect_evm_attacks import CoinspectEVMAttacksSource
from rugbrain.ingest_sources.crpwarner_rug_events import CRPWarnerRugEventsSource
from rugbrain.ingest_sources.crypto_rekts import CryptoRektsSource
from rugbrain.ingest_sources.defihacklabs import DeFiHackLabsSource
from rugbrain.ingest_sources.defillama import DeFiLlamaSource
from rugbrain.ingest_sources.deflabx import DeFiLabXSolRPDSource
from rugbrain.ingest_sources.eips import BIPsSource, EIPsSource
from rugbrain.ingest_sources.emperor_cftc import EmperorMewCFTCSource
from rugbrain.ingest_sources.feeds import (
    CFTCPressReleasesSource,
    ImmunefiBlogSource,
    NeodymeBlogSource,
    TrailOfBitsBlogSource,
    ZellicBlogSource,
)
from rugbrain.ingest_sources.forta_malicious_contracts import FortaMaliciousContractsSource
from rugbrain.ingest_sources.fraudcoins_signatures import FraudCoinsSignaturesSource
from rugbrain.ingest_sources.hf_audit_archive import GCF3711AuditArchiveSource
from rugbrain.ingest_sources.incidents import (
    BlockSecDefiPocSource,
    GutteDeFiSource,
    ImmunefiHackAnalysesSource,
    SlowMistOpenReportsSource,
)
from rugbrain.ingest_sources.l2beat import L2BeatSource
from rugbrain.ingest_sources.mev import MEVBoostRelaySource, MEVBoostSource
from rugbrain.ingest_sources.openphish import OpenPhishSource
from rugbrain.ingest_sources.opensanctions import OpenSanctionsSource
from rugbrain.ingest_sources.openzeppelin import OpenZeppelinSource
from rugbrain.ingest_sources.ossf_malicious import OSSFMaliciousPackagesSource
from rugbrain.ingest_sources.phishdestroy import PhishDestroySource
from rugbrain.ingest_sources.privacy import TornadoSource
from rugbrain.ingest_sources.rekt import RektSource
from rugbrain.ingest_sources.safe import SafeSource
from rugbrain.ingest_sources.samczsun import SamczsunSource
from rugbrain.ingest_sources.scythe_radar_datasets import ScytheRadarDatasetsSource
from rugbrain.ingest_sources.seal911 import Seal911Source
from rugbrain.ingest_sources.swc import SWCSource
from rugbrain.ingest_sources.trailofbits_publications import TrailOfBitsPublicationsSource
from rugbrain.ingest_sources.urlhaus import URLHausSource
from rugbrain.ingest_sources.vulnerability_severity import VulnerabilitySeveritySource
from rugbrain.ingest_sources.whitepapers import WhitepapersSource
from rugbrain.ingest_sources.yaudit_fork_bugs import YAuditForkBugsSource
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
    ChainabuseSource(),
    CryptoRektsSource(),
    PhishDestroySource(),
    DeFiLabXSolRPDSource(),
    Seal911Source(),
    TrailOfBitsPublicationsSource(),
    GCF3711AuditArchiveSource(),
    EmperorMewCFTCSource(),
    OpenSanctionsSource(),
    SamczsunSource(),
    BlockSecPhishingContractsSource(),
    OSSFMaliciousPackagesSource(),
    TrailOfBitsBlogSource(),
    ZellicBlogSource(),
    ImmunefiBlogSource(),
    NeodymeBlogSource(),
    CFTCPressReleasesSource(),
    CISAKevSource(),
    OpenPhishSource(),
    URLHausSource(),
    YAuditForkBugsSource(),
    CoinspectEVMAttacksSource(),
    FraudCoinsSignaturesSource(),
    ScytheRadarDatasetsSource(),
    FortaMaliciousContractsSource(),
    CRPWarnerRugEventsSource(),
    VulnerabilitySeveritySource(),
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

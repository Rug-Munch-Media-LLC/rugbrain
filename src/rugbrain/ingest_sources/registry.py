"""The registry: every ingest source instance, and lookup by name.

One job — hold the catalogue and answer "which source is called X". The
sources themselves each live in their own module; the fetching machinery
lives in :mod:`rugbrain.ingest_sources.fetch`, the failure types in
:mod:`rugbrain.ingest_sources.outcome`, the CLI in
:mod:`rugbrain.ingest_sources.cli`.

EIPs and BIPs are separate sources so each can be fetched/limited
independently.

The catalogue
-------------

Each source implements ``Source.fetch() -> list[IngestDoc]`` (see ``base.py``)
and normalizes upstream GitHub/API content into ``IngestDoc`` records
carrying a semantic ``doc_type``:

  * ``defihacklabs``           — real DeFi exploit PoCs      (exploit_poc)
  * ``swc_registry``           — SWC weakness taxonomy       (vulnerability_pattern)
  * ``eips`` / ``bips``        — protocol standards          (standard)
  * ``openzeppelin_contracts`` — audited Solidity code       (contract_code)
  * ``rekt_news``              — post-mortem articles        (post_mortem)
  * ``crypto_whitepapers``     — project whitepapers         (whitepaper)
  * ``defillama``              — live TVL/market context     (market_data)
  * ``market_health``          — CoinGecko global/FNG/derivatives (market_data)
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
  * ``devdocs_static``         — x402/MCP specs + Python/Rust/Go/Solidity docs
    (language_docs / protocol_spec / sdk_docs)
  * ``eng_blogs``              — Solana/Base/Flashbots/Paradigm/a16z crypto
    (engineering_blog)
  * ``ossf_malicious_packages`` / ``cisa_kev`` — vulnerability registries
    (vulnerability_pattern)
"""

from __future__ import annotations

from rugbrain.ingest_sources.base import Source
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
from rugbrain.ingest_sources.devdocs import DevDocsSource
from rugbrain.ingest_sources.eips import BIPsSource, EIPsSource
from rugbrain.ingest_sources.emperor_cftc import EmperorMewCFTCSource
from rugbrain.ingest_sources.eng_blogs import EngineeringBlogsSource
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
from rugbrain.ingest_sources.market_health import MarketHealthSource
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

#: All registered source instances.
SOURCES: tuple[Source, ...] = (
    DeFiHackLabsSource(),
    SWCSource(),
    EIPsSource(),
    BIPsSource(),
    OpenZeppelinSource(),
    RektSource(),
    WhitepapersSource(),
    DeFiLlamaSource(),
    MarketHealthSource(),
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
    DevDocsSource(),
    EngineeringBlogsSource(),
    VulnerabilitySeveritySource(),
)

#: Semantic doc_type → source names (for quick lookup / filtering).
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


__all__ = ["DOC_TYPES", "SOURCES", "get_source", "list_sources"]

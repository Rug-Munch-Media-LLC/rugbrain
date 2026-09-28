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

A zero-document source is NOT automatically healthy: HTTP failures are
recorded on the :mod:`fetch_state` channel with an operator reason token, and
``fetch_source`` / the CLI turn that into an error count and a non-zero exit.
See ``fetch_state`` for the protocol.

Usage::

    python -m rugbrain.ingest_sources --source swc_registry --limit 3
    python -m rugbrain.ingest_sources --list
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field

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
from rugbrain.ingest_sources.fetch_state import (
    FetchError,
    clear_fetch_error,
    take_fetch_error,
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


class FetchFailure(Exception):
    """A source fetch hit an HTTP/transport failure (reason + partial docs).

    Raised by :func:`fetch_source` so a failure can never be reported as a
    successful zero. ``reason`` is the operator token (``github_not_found``,
    ``github_rate_limited``, ``network_timeout``, …) and ``docs`` keeps any
    partial result so a caller can still store what did arrive.
    """

    def __init__(self, error: FetchError, *, name: str, docs: list[IngestDoc]) -> None:
        super().__init__(f"source_fetch_failed name={name} {error.as_log_line()}")
        self.reason = error
        self.name = name
        self.docs = docs


@dataclass(frozen=True)
class SourceResult:
    """Outcome of one source fetch: documents + the failure reason, if any.

    ``error is None`` means a clean run — including a legitimately empty
    source. ``error`` carries the reason token that makes a dead source
    visible instead of a silent zero.
    """

    name: str
    docs: list[IngestDoc] = field(default_factory=list)
    error: FetchError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def count(self) -> int:
        return len(self.docs)


async def fetch_source(name: str, limit: int | None = None) -> list[IngestDoc]:
    """Fetch *name*; ``[]`` when unknown, or on a failure that is *recorded*.

    Still returns ``list[IngestDoc]`` — the contract does not change. A failed
    fetch is no longer indistinguishable from a healthy empty source, though:
    it is logged with a ``reason=`` token and re-raised, so the caller MUST
    wrap this in ``try/except`` (or use :func:`fetch_source_result`) to report
    it. The previous behaviour — swallow the failure and hand back a bare
    ``[]`` the caller then counted as a successful zero — is what let 15+
    sources die silently behind a green exit.
    """
    src = get_source(name)
    if src is None:
        log.warning("unknown_source name=%s available=%s", name, list_sources())
        return []
    clear_fetch_error()
    docs = await src.fetch(limit=limit)
    err = take_fetch_error()
    if err is not None:
        raise FetchFailure(err, name=name, docs=docs)
    return docs


async def fetch_source_result(name: str, limit: int | None = None) -> SourceResult:
    """Outcome-bearing fetch: never raises, reports failure explicitly."""
    try:
        return SourceResult(name=name, docs=await fetch_source(name, limit), error=None)
    except FetchFailure as exc:
        return SourceResult(name=exc.name, docs=exc.docs, error=exc.reason)


async def fetch_all(limit: int | None = None) -> dict[str, list[IngestDoc]]:
    """Fetch every source; returns ``{source_name: [IngestDoc, ...]}``.

    A failing source is re-raised as :class:`FetchFailure` *after* logging, so
    a batch run cannot be mistaken for a full pass. Use
    :func:`fetch_all_results` to collect outcomes without aborting.
    """
    out: dict[str, list[IngestDoc]] = {}
    for src in SOURCES:
        out[src.name] = await fetch_source(src.name, limit)
        log.info("source_done name=%s docs=%d", src.name, len(out[src.name]))
    return out


async def fetch_all_results(limit: int | None = None) -> list[SourceResult]:
    """Fetch every source, one failure never aborts the batch."""
    results: list[SourceResult] = []
    for src in SOURCES:
        result = await fetch_source_result(src.name, limit)
        results.append(result)
        state = "ok" if result.error is None else f"ERROR {result.error.as_log_line()}"
        log.info("source_done name=%s docs=%d %s", result.name, len(result.docs), state)
    return results


def _print_result(result: SourceResult) -> None:
    """One operator line: count plus the reason when the source is dead."""
    if result.ok:
        print(f"{result.name}: fetched {result.count} docs")
        return
    print(
        f"{result.name}: fetched {result.count} docs — FAILED {result.error.as_log_line()}",
        file=sys.stderr,
    )


def _main() -> int:
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
        return 0
    if args.source:
        src = get_source(args.source)
        if src is None:
            print(f"unknown source: {args.source}; available: {list_sources()}")
            return 2
        result = asyncio.run(fetch_source_result(args.source, args.limit))
        _print_result(result)
        for d in result.docs[:5]:
            print(f"  - {d.title or d.doc_id} | {d.url}")
        # A failed fetch is a non-zero exit: a green run must mean documents
        # were fetched, not that the HTTP layer quietly returned nothing.
        return 0 if result.ok else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())


__all__ = [
    "DOC_TYPES",
    "SOURCES",
    "FetchFailure",
    "IngestDoc",
    "Source",
    "SourceResult",
    "fetch_all",
    "fetch_all_results",
    "fetch_source",
    "fetch_source_result",
    "get_source",
    "list_sources",
    "to_crawl_document",
]

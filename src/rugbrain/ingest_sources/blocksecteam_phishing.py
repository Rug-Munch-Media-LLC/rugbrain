"""blocksecteam/phishing_contract_sigmetrics25 — phishing contracts dataset.

Source: https://github.com/blocksecteam/phishing_contract_sigmetrics25 —
research-grade dataset behind "Phishing Tactics Are Evolving: An Empirical
Study of Phishing Contracts on Ethereum" (SIGMETRICS 2025): 37,000+ phishing
contracts incl. drainer wallet addresses. The repo publishes plain-text
files at the root; ``phishing_contracts.log`` is one Ethereum address per
line (37,653 lines verified live).

Routes to ``scam_intel_v2`` (live curated scam intelligence) — on-chain
drainer addresses are current-activity evidence, consistent with the
``blacklists`` source. One ``IngestDoc`` per address (doc_type
``scam_entity``, labels ``drainer``/``phishing``).

Design rules (consistent with ``base.py`` / ``blacklists.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``blocksecteam_phishing_contracts:<address>``)
    keeps re-ingestion idempotent.
  * Credentials: none — raw.githubusercontent content.
"""

from __future__ import annotations

import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "scam_intel_v2"
DEFAULT_CAP = 10_000
REPO = "blocksecteam/phishing_contract_sigmetrics25"
REF = "main"
LOG_URL = f"https://raw.githubusercontent.com/{REPO}/{REF}/phishing_contracts.log"
SOURCE_URL = f"https://github.com/{REPO}"


def _is_address(value: str) -> bool:
    return len(value) >= 40 and value[:2].lower() == "0x"


def parse_address_log(text: str) -> list[str]:
    """Parse one-address-per-line log; keeps only valid Ethereum addresses."""
    out: list[str] = []
    for line in (text or "").splitlines():
        addr = line.strip().lower()
        if addr and not addr.startswith("#") and _is_address(addr):
            out.append(addr)
    return out


def _make_doc(address: str) -> IngestDoc:
    metadata = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "address",
        "entity": address,
        "labels": ["drainer", "phishing"],
        "category": "phishing",
        "feed": "phishing_contracts",
        "source": "blocksecteam_phishing_contracts",
        "source_url": SOURCE_URL,
        "knowledge_type": "blacklist",
        "attack_vector": "phishing",
        "chain": "EVM",
        "ecosystem": "EVM",
        "severity": "critical",
    }
    return IngestDoc(
        content=(
            f"blocksecteam phishing contract address: {address}\n"
            f"Labels: drainer, phishing\n"
            f"Source: {SOURCE_URL}\n"
        ),
        doc_id=f"blocksecteam_phishing_contracts:{address}",
        metadata=metadata,
        doc_type="scam_entity",
        source="blocksecteam_phishing_contracts",
        title=f"phishing contract address ({address[:12]}…)",
        url=SOURCE_URL,
    )


class BlockSecPhishingContractsSource(Source):
    """blocksecteam phishing_contract_sigmetrics25 — phishing contract dataset.

    Emits one ``IngestDoc`` per drainer/phishing contract address into
    ``scam_intel_v2`` (doc_type ``scam_entity``).
    """

    name = "blocksecteam_phishing_contracts"
    doc_type = "scam_entity"
    default_collection = DEFAULT_COLLECTION
    description = "BlockSec SIGMETRICS 2025 phishing contracts dataset — 37k drainer addresses (scam_intel_v2)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(LOG_URL, timeout=self.timeout)
            text = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("blocksecteam_phishing_fetch_error err=%s", str(e)[:160])
            text = ""
        addresses = parse_address_log(text)
        if not addresses:
            log.warning("blocksecteam_phishing_fetch_empty")
            return []
        log.info("blocksecteam_phishing_feed_ok addresses=%d (cap=%d)", len(addresses), cap)
        return [_make_doc(a) for a in addresses[:cap]]


__all__ = [
    "DEFAULT_COLLECTION",
    "BlockSecPhishingContractsSource",
    "parse_address_log",
]

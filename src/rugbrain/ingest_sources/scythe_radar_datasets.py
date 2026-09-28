"""scythe-radar-datasets — dead/rugged token archive + OFAC addresses (CC0-1.0).

Source: https://github.com/scytheradar/scythe-radar-datasets (CC0-1.0, verified
2026-09-28). Two machine-readable CSVs:

  * ``data/dead-token-archive.csv`` — 19 documented dead/rugged tokens with
    ``contract_address, token_name, chain, status, outcome, mechanism,
    mechanism_en, early_warning, victims_note, timeline_span, detail_url``.
    This is a curated, human-documented rug archive: the ``mechanism`` field is
    exactly the scam-signature vocabulary (unlimited mint, hidden owner, LP
    drained …), so it is the highest-precision seed set available for
    ``rug_events`` / ``scam_signatures``.
  * ``data/ofac-sanctioned-addresses.csv`` — 492 OFAC-sanctioned crypto
    addresses across chains with ``sanctions_program`` / ``entity_name``.

Emits two doc families: dead tokens → ``rug_events`` (doc_type ``rug_event``,
STRUCTURED) and sanctioned addresses → ``known_scams`` (doc_type
``sanction_entity``).

Design rules (consistent with ``base.py`` / ``deflabx.py``): fail-open per
file, deterministic ``doc_id``, no credentials (raw.githubusercontent.com).
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

RUG_COLLECTION = "rug_events"
SCAM_COLLECTION = "known_scams"
REPO = "scytheradar/scythe-radar-datasets"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/data"
DEAD_TOKENS_URL = f"{RAW}/dead-token-archive.csv"
OFAC_URL = f"{RAW}/ofac-sanctioned-addresses.csv"
REPO_URL = f"https://github.com/{REPO}"

# status (zh) → normalized outcome. The upstream archive is bilingual.
_STATUS_MAP = {
    "已拔池": "lp_drained",
    "已归零": "went_to_zero",
    "重启盘": "rugged_relaunch",
    "lp_drained": "lp_drained",
    "went_to_zero": "went_to_zero",
    "rugged_relaunch": "rugged_relaunch",
}


def _rows(text: str) -> list[dict[str, str]]:
    """Parse a CSV payload into row dicts; ``[]`` on any parse failure."""
    try:
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text or ""))
        ]
    except Exception as e:
        log.warning("scythe_csv_parse_failed err=%s", str(e)[:160])
        return []


def dead_token_to_doc(row: dict[str, str]) -> IngestDoc | None:
    """One documented dead/rugged token → ``rug_events`` IngestDoc.

    Returns ``None`` when the row has no contract address — the identity key.
    """
    address = (row.get("contract_address") or "").strip()
    if not address:
        return None
    chain = (row.get("chain") or "").strip() or "unknown"
    status = (row.get("status_en") or row.get("status") or "").strip()
    outcome = _STATUS_MAP.get((row.get("status") or "").strip(), status.lower() or "unknown")
    mechanism = (row.get("mechanism_en") or row.get("mechanism") or "").strip()
    detail = (row.get("detail_url") or "").strip()

    metadata: dict[str, Any] = {
        "collection": RUG_COLLECTION,
        "entity_type": "token",
        "entity": address,
        "token_address": address,
        "chain": chain,
        "severity": "critical",
        "category": "rug-pull",
        "source": "scythe_radar",
        "source_url": REPO_URL,
        "knowledge_type": "documented_rug",
        "labels": ["rugpull", "dead-token"],
    }
    if outcome:
        metadata["rug_outcome"] = outcome
    if status:
        metadata["status"] = status
    if mechanism:
        metadata["rug_mechanism"] = mechanism
        metadata["labels"] = sorted({*metadata["labels"], "documented-mechanism"})
    for src_key, dst in (("early_warning", "early_warning"), ("victims_note", "victims_note")):
        value = (row.get(src_key) or "").strip()
        if value:
            metadata[dst] = value
    span = (row.get("timeline_span") or "").strip()
    if span:
        metadata["timeline_span"] = span
    name = (row.get("token_name_en") or row.get("token_name") or "").strip()
    if name:
        metadata["token_name"] = name

    lines = [f"Documented dead/rugged token: {name or address}", f"Chain: {chain}"]
    if outcome:
        lines.append(f"Rug outcome: {outcome}")
    if mechanism:
        lines.append(f"Rug mechanism: {mechanism}")
    warn = (row.get("early_warning") or "").strip()
    if warn:
        lines.append(f"Early warning signal: {warn}")
    span_val = (row.get("timeline_span") or "").strip()
    if span_val:
        lines.append(f"Timeline: {span_val}")

    return IngestDoc(
        content="\n".join(lines) + "\n",
        doc_id=f"scythe_radar:dead_token:{chain}:{address}",
        metadata=metadata,
        doc_type="rug_event",
        source="scythe_radar",
        title=f"Documented rug — {name or address[:12]} ({chain})",
        url=detail or REPO_URL,
    )


def ofac_to_doc(row: dict[str, str]) -> IngestDoc | None:
    """One OFAC-sanctioned crypto address → ``known_scams`` IngestDoc."""
    address = (row.get("address") or "").strip()
    if not address:
        return None
    chain = (row.get("chain") or "").strip() or "unknown"
    entity = (row.get("entity_name") or "").strip()
    program = (row.get("sanctions_program") or "").strip()
    metadata: dict[str, Any] = {
        "collection": SCAM_COLLECTION,
        "entity_type": "wallet",
        "entity": address,
        "address": address,
        "chain": chain,
        "severity": "critical",
        "category": "sanctions",
        "source": "scythe_radar",
        "source_url": REPO_URL,
        "knowledge_type": "ofac_sanction",
        "labels": ["ofac", "sanctioned", "crypto-address"],
    }
    if entity:
        metadata["entity_name"] = entity
    if program:
        metadata["sanctions_program"] = program
    sdn = (row.get("sdn_entry") or "").strip()
    if sdn:
        metadata["sdn_entry"] = sdn

    lines = [f"OFAC-sanctioned crypto address: {address}", f"Chain: {chain}"]
    if entity:
        lines.append(f"Sanctioned entity: {entity}")
    if program:
        lines.append(f"Sanctions program: {program}")

    return IngestDoc(
        content="\n".join(lines) + "\n",
        doc_id=f"scythe_radar:ofac:{chain}:{address}",
        metadata=metadata,
        doc_type="sanction_entity",
        source="scythe_radar",
        title=f"OFAC-sanctioned address — {entity or address[:12]}",
        url=REPO_URL,
    )


class ScytheRadarDatasetsSource(Source):
    """scythe-radar-datasets — documented dead tokens + OFAC crypto addresses (CC0)."""

    name = "scythe_radar_datasets"
    doc_type = "rug_event"
    default_collection = RUG_COLLECTION
    description = "scythe-radar-datasets — documented dead tokens (rug_events) + OFAC addresses"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        docs: list[IngestDoc] = []
        for url, mapper in ((DEAD_TOKENS_URL, dead_token_to_doc), (OFAC_URL, ofac_to_doc)):
            try:
                r = await client.get(url, timeout=self.timeout)
                if r.status_code != 200:
                    log.debug("scythe_non200 url=%s status=%d", url, r.status_code)
                    continue
                rows = _rows(r.text)
            except Exception as e:
                log.warning("scythe_fetch_failed url=%s err=%s", url, str(e)[:160])
                continue
            for row in rows:
                doc = mapper(row)
                if doc is not None:
                    docs.append(doc)
        if limit:
            docs = docs[:limit]
        if not docs:
            log.warning("scythe_radar_fetch_empty")
        else:
            log.info("scythe_radar_fetch_ok docs=%d", len(docs))
        return docs


__all__ = [
    "DEAD_TOKENS_URL",
    "OFAC_URL",
    "REPO",
    "RUG_COLLECTION",
    "SCAM_COLLECTION",
    "ScytheRadarDatasetsSource",
    "dead_token_to_doc",
    "ofac_to_doc",
]

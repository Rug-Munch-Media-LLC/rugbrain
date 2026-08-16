"""DeFiLabX/SolRPDS — Solana rug-pull dataset (defi_exploits).

Source: https://github.com/DeFiLabX/SolRPDS — the first public Solana
rug-pull dataset (3.69B txs, 22,195 rug tokens, ~$151M losses; CODASPY
2025). The repo publishes per-year JSON under ``dataset/json/YYYY.json``
(2021-2023 verified live) where each record describes one liquidity pool
that was part of a rug pull: the token mint, pool address, added/removed
liquidity, activity timestamps and an inactivity status.

Complements the EVM-only incident feeds already in ``defi_exploits`` with
Solana-native rug knowledge. One ``IngestDoc`` is emitted per rug token
(``MINT``), carrying the pool/activity metadata.

Design rules (consistent with ``base.py`` / ``blacklists.py``):

  * Fail-open: a missing year file or a parse error logs + continues; a
    fully failed source returns ``[]``.
  * Deterministic ``doc_id`` (``deflabx_solrpds:<mint>:<year>``) keeps
    re-ingestion idempotent.
  * Credentials: none — raw.githubusercontent content.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "defi_exploits"
DEFAULT_CAP = 5_000
REPO = "DeFiLabX/SolRPDS"
REF = "main"
YEARS = (2021, 2022, 2023)  # verified live on 2026-08-16; 2024+ not published
RAW_BASE = "https://raw.githubusercontent.com/DeFiLabX/SolRPDS/main/dataset/json"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def wallet_to_doc(record: dict[str, Any], year: int) -> IngestDoc | None:
    """Normalize one SolRPDS pool record into a rug-token IngestDoc.

    Returns ``None`` for records without a ``MINT`` (token address), so
    empty upstream rows never create blank points.
    """
    mint = str(record.get("MINT") or "").strip()
    if not mint:
        return None
    pool = str(record.get("LIQUIDITY_POOL_ADDRESS") or "").strip()
    added = str(record.get("TOTAL_ADDED_LIQUIDITY") or "").strip()
    removed = str(record.get("TOTAL_REMOVED_LIQUIDITY") or "").strip()
    ratio = str(record.get("ADD_TO_REMOVE_RATIO") or "").strip()
    first_ts = str(record.get("FIRST_POOL_ACTIVITY_TIMESTAMP") or "").strip()
    last_ts = str(record.get("LAST_POOL_ACTIVITY_TIMESTAMP") or "").strip()
    status = str(record.get("INACTIVITY_STATUS") or "").strip()

    content_parts = [
        f"Solana rug-pull token: {mint}",
        f"Year: {year}",
        f"Liquidity pool: {pool or 'unknown'}",
    ]
    if added:
        content_parts.append(f"Total added liquidity: {added}")
    if removed:
        content_parts.append(f"Total removed liquidity: {removed}")
    if ratio:
        content_parts.append(f"Add/remove ratio: {ratio}")
    if first_ts:
        content_parts.append(f"First pool activity: {first_ts}")
    if last_ts:
        content_parts.append(f"Last pool activity: {last_ts}")
    if status:
        content_parts.append(f"Status: {status}")

    metadata = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "token",
        "entity": mint,
        "token_address": mint,
        "labels": ["rugpull"],
        "category": "rug-pull",
        "chain": "Solana",
        "ecosystem": "Solana",
        "severity": "critical",
        "year": year,
        "source": "deflabx_solrpds",
        "source_url": f"https://github.com/{REPO}",
        "knowledge_type": "rug_pull_dataset",
    }
    if pool:
        metadata["pool_address"] = pool
    if added:
        metadata["total_added_liquidity"] = added
    if removed:
        metadata["total_removed_liquidity"] = removed
    if ratio:
        metadata["add_to_remove_ratio"] = ratio
    if first_ts:
        metadata["first_pool_activity"] = first_ts
    if last_ts:
        metadata["last_pool_activity"] = last_ts
    if status:
        metadata["inactivity_status"] = status

    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"deflabx_solrpds:{mint}:{year}",
        metadata=metadata,
        doc_type="scam_entity",
        source="deflabx_solrpds",
        title=f"Solana rug-pull token ({mint[:12]}…) — {year}",
        url=f"https://github.com/{REPO}",
    )


def parse_year_json(text: str) -> list[dict[str, Any]]:
    """Parse one year's ``dataset/json/YYYY.json`` payload (list of records)."""
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


class DeFiLabXSolRPDSource(Source):
    """DeFiLabX/SolRPDS — Solana rug-pull token dataset.

    Fetches the per-year JSON exports (2021-2023), normalizes one
    ``IngestDoc`` per rug token into ``defi_exploits`` (doc_type
    ``scam_entity``). Missing years fail open with a warning.
    """

    name = "deflabx_solrpds"
    doc_type = "scam_entity"
    default_collection = DEFAULT_COLLECTION
    description = (
        "DeFiLabX SolRPDS — first public Solana rug-pull dataset (22k rug tokens, defi_exploits)"
    )
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        docs: list[IngestDoc] = []
        for year in YEARS:
            url = f"{RAW_BASE}/{year}.json"
            try:
                r = await client.get(url, timeout=self.timeout)
                if r.status_code != 200:
                    log.debug("deflabx_year_missing year=%d status=%d", year, r.status_code)
                    continue
                records = parse_year_json(r.text)
            except Exception as e:
                log.warning("deflabx_year_error year=%d err=%s", year, str(e)[:160])
                continue
            for record in records:
                doc = wallet_to_doc(record, year)
                if doc:
                    docs.append(doc)
                    if len(docs) >= cap:
                        break
            if len(docs) >= cap:
                break
        if not docs:
            log.warning("deflabx_solrpds_fetch_empty")
        else:
            log.info("deflabx_solrpds_feed_ok tokens=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DeFiLabXSolRPDSource",
    "parse_year_json",
    "wallet_to_doc",
]

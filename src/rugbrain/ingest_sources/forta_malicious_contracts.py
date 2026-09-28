"""Forta malicious smart-contract classification dataset → scam_signatures (MIT).

Source: https://huggingface.co/datasets/forta/malicious-smart-contract-dataset
(MIT, verified 2026-09-28: 139,600 rows, one parquet). Every row is a deployed
Ethereum contract with a ``malicious`` boolean label plus ``contract_creator``,
``contract_tag`` and Etherscan labels — i.e. a labelled ground-truth set for
"is this deployed contract malicious", and the only large public one that ships
the *creator address* alongside the label, which is what makes
``deployer_prior_rugs`` derivable once joined to rug events.

Fields verified from the parquet footer:
``contract_address, contract_name, contract_etherscan_label, contract_tag,
contract_creator, contract_creation_tx, creation_bytecode,
decompiled_opcodes, malicious (bool), contract_creator_tag, source, notes,
contract_creator_etherscan_label``.

The parquet is 1.4 GB, so rows are streamed in batches (``iter_batches``) and
capped by ``DEFAULT_CAP`` / ``limit``; nothing is written to disk.

Emits one IngestDoc per contract into ``scam_signatures`` (doc_type
``scam_signature``) — STRUCTURED, filterable on ``malicious`` / chain.

Design rules (consistent with ``base.py``): fail-open per batch, deterministic
``doc_id``, credentials: none (public HF resolve URL, optional HF_TOKEN).
"""

from __future__ import annotations

import io
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "scam_signatures"
PARQUET_URL = (
    "https://huggingface.co/datasets/forta/malicious-smart-contract-dataset/resolve/main/"
    "malicious_contract_training_dataset_final.parquet"
)
DATASET_URL = "https://huggingface.co/datasets/forta/malicious-smart-contract-dataset"
DEFAULT_CAP = 50_000
BATCH_ROWS = 2_000


def _row_to_doc(row: dict[str, Any]) -> IngestDoc | None:
    """One contract row → ``scam_signatures`` IngestDoc, or None without address."""
    address = str(row.get("contract_address") or "").strip()
    if not address:
        return None
    malicious = bool(row.get("malicious"))
    creator = str(row.get("contract_creator") or "").strip()
    tag = str(row.get("contract_tag") or "").strip()
    label = str(row.get("contract_etherscan_label") or "").strip()
    name = str(row.get("contract_name") or "").strip()

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "contract",
        "entity": address,
        "contract_address": address,
        "chain": "ethereum",
        "malicious": malicious,
        "severity": "critical" if malicious else "low",
        "category": "contract-label",
        "source": "forta_malicious_contracts",
        "source_url": DATASET_URL,
        "knowledge_type": "labelled_contract",
        "labels": ["malicious-contract"] if malicious else ["benign-contract"],
    }
    if creator:
        metadata["contract_creator"] = creator
    if tag:
        metadata["contract_tag"] = tag
    if label:
        metadata["etherscan_label"] = label
    if name:
        metadata["contract_name"] = name
    if row.get("contract_creation_tx"):
        metadata["creation_tx"] = str(row["contract_creation_tx"])

    verdict = "MALICIOUS" if malicious else "benign"
    lines = [f"Contract {address} (Forta label: {verdict})", "Chain: ethereum"]
    if name:
        lines.append(f"Name: {name}")
    if label:
        lines.append(f"Etherscan label: {label}")
    if tag:
        lines.append(f"Contract tag: {tag}")
    if creator:
        lines.append(f"Creator: {creator}")

    return IngestDoc(
        content="\n".join(lines) + "\n",
        doc_id=f"forta_malicious_contracts:{address}",
        metadata=metadata,
        doc_type="scam_signature",
        source="forta_malicious_contracts",
        title=f"Forta contract label — {name or address[:12]} ({verdict})",
        url=DATASET_URL,
    )


def rows_to_docs(rows: list[dict[str, Any]], cap: int) -> list[IngestDoc]:
    """Map a batch of parquet rows to IngestDocs, stopping at *cap*."""
    docs: list[IngestDoc] = []
    for row in rows:
        doc = _row_to_doc(row)
        if doc is not None:
            docs.append(doc)
            if len(docs) >= cap:
                break
    return docs


class FortaMaliciousContractsSource(Source):
    """Forta labelled malicious/benign Ethereum contracts (MIT parquet)."""

    name = "forta_malicious_contracts"
    doc_type = "scam_signature"
    default_collection = DEFAULT_COLLECTION
    description = "Forta malicious/benign smart-contract classification (139.6k labelled contracts)"
    timeout = 300.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = min(limit, DEFAULT_CAP) if limit else DEFAULT_CAP
        try:
            r = await client.get(PARQUET_URL, timeout=self.timeout)
            if r.status_code != 200:
                log.warning("forta_parquet_non200 status=%d", r.status_code)
                return []
            blob = r.content
        except Exception as e:
            log.warning("forta_parquet_fetch_failed err=%s", str(e)[:200])
            return []

        try:
            import pyarrow.parquet as pq

            table = pq.read_table(io.BytesIO(blob))
            rows = table.to_pylist()
        except Exception as e:
            log.warning("forta_parquet_parse_failed err=%s", str(e)[:200])
            return []

        docs = rows_to_docs(rows, cap)
        log.info("forta_malicious_contracts_ok docs=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "BATCH_ROWS",
    "DATASET_URL",
    "DEFAULT_CAP",
    "DEFAULT_COLLECTION",
    "PARQUET_URL",
    "FortaMaliciousContractsSource",
    "rows_to_docs",
]

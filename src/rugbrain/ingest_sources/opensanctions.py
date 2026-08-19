"""OpenSanctions bulk — sanctions/watchlist database (regulation_enforcement).

Source: https://data.opensanctions.org/datasets/latest/default/ — the
deduplicated OpenSanctions dataset (1.18M persons, 288K orgs, ~13.9K crypto
addresses from OFAC/UK/EU/Israel). Crypto-relevant entities are
FollowTheMoney ``CryptoWallet`` records inside ``entities.ftm.json``.

``entities.ftm.json`` is huge (multi-GB) and **line-delimited JSON** — one
entity object per line (verified live). This source streams the file and
keeps only ``CryptoWallet`` entities, bounded by ``limit``/``DEFAULT_CAP``,
so a full download never happens. Each wallet becomes one ``IngestDoc`` into
``regulation_enforcement`` (doc_type ``sanction_entity``).

Design rules (consistent with ``base.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``opensanctions:<entity_id>``) — FollowTheMoney
    entity IDs are stable across exports → idempotent re-ingestion.
  * Credentials: none (non-commercial bulk license).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "regulation_enforcement"
DEFAULT_CAP = 5_000
ENTITIES_URL = "https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"
SOURCE_URL = "https://opensanctions.org/"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def _iter_lines(client: httpx.AsyncClient, url: str, timeout_s: float) -> AsyncIterator[str]:
    """Stream a URL and yield complete lines (bounded by response stream)."""
    async with client.stream("GET", url, timeout=timeout_s) as r:
        if r.status_code != 200:
            log.warning("opensanctions_stream_status status=%d", r.status_code)
            return
        buffer = b""
        async for chunk in r.aiter_bytes():
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace")
        if buffer.strip():
            yield buffer.decode("utf-8", errors="replace")


def _ftm_first(properties: dict[str, Any], key: str) -> str:
    vals = properties.get(key)
    if isinstance(vals, list):
        return str(vals[0] or "").strip() if vals else ""
    return str(vals or "").strip()


def wallet_entity_to_doc(entity: dict[str, Any]) -> IngestDoc | None:
    """Normalize one FollowTheMoney CryptoWallet entity into an IngestDoc.

    Returns ``None`` for entities without an address or id (never emit a
    blank point).
    """
    entity_id = str(entity.get("id") or "").strip()
    props = entity.get("properties") if isinstance(entity.get("properties"), dict) else {}
    address = _ftm_first(props, "address")  # type: ignore[arg-type]
    if not entity_id and not address:
        return None
    caption = str(entity.get("caption") or "").strip()
    asset = _ftm_first(props, "asset")  # type: ignore[arg-type]
    blockchain = _ftm_first(props, "blockchain") or _ftm_first(props, "network")  # type: ignore[arg-type]
    raw_datasets = entity.get("datasets")
    datasets = [str(d) for d in raw_datasets if str(d)] if isinstance(raw_datasets, list) else []
    target = bool(entity.get("target"))

    content_parts = [
        f"OpenSanctions crypto wallet: {caption or address or entity_id}",
    ]
    if address:
        content_parts.append(f"Address: {address}")
    if asset:
        content_parts.append(f"Asset: {asset}")
    if blockchain:
        content_parts.append(f"Blockchain: {blockchain}")
    if datasets:
        content_parts.append("Source datasets: " + ", ".join(sorted(set(datasets))))
    if target:
        content_parts.append("Target: yes (sanctioned entity)")

    metadata = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "address",
        "entity": address or entity_id,
        "address": address,
        "labels": ["sanctioned"] if target else ["watchlist"],
        "category": "sanctions",
        "source_url": SOURCE_URL,
        "knowledge_type": "sanction_entity",
        "ecosystem": "multi",
        "severity": "critical" if target else "medium",
        "datasets": datasets,
    }
    if caption:
        metadata["name"] = caption
    if asset:
        metadata["asset"] = asset
    if blockchain:
        metadata["blockchain"] = blockchain

    key = entity_id or _content_hash(address)
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"opensanctions:{key}",
        metadata=metadata,
        doc_type="sanction_entity",
        source="opensanctions",
        title=f"OpenSanctions crypto wallet — {caption or address or entity_id}",
        url=SOURCE_URL,
    )


class OpenSanctionsSource(Source):
    """OpenSanctions bulk — sanctions/watchlist crypto wallets.

    Streams ``entities.ftm.json`` (JSON Lines), keeps only ``CryptoWallet``
    entities, and emits one ``IngestDoc`` per wallet into
    ``regulation_enforcement`` (doc_type ``sanction_entity``). Bounded by
    ``limit`` (default cap 5,000) — the multi-GB file is never fully loaded.
    """

    name = "opensanctions"
    doc_type = "sanction_entity"
    default_collection = DEFAULT_COLLECTION
    description = (
        "OpenSanctions bulk — sanctions/watchlist crypto wallet addresses (regulation_enforcement)"
    )
    timeout = 120.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        docs: list[IngestDoc] = []
        try:
            async for line in _iter_lines(client, ENTITIES_URL, self.timeout):
                line = line.strip()
                if not line or line in ("[", "]", ","):
                    continue
                try:
                    entity = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entity, dict) or entity.get("schema") != "CryptoWallet":
                    continue
                doc = wallet_entity_to_doc(entity)
                if doc:
                    docs.append(doc)
                    if len(docs) >= cap:
                        break
        except Exception as e:
            log.warning("opensanctions_fetch_error err=%s", str(e)[:200])
            return []
        if not docs:
            log.warning("opensanctions_fetch_empty (no CryptoWallet entities found)")
        else:
            log.info("opensanctions_feed_ok wallets=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "ENTITIES_URL",
    "OpenSanctionsSource",
    "_iter_lines",
    "wallet_entity_to_doc",
]

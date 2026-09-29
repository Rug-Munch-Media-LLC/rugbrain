"""OpenSanctions bulk — sanctions/watchlist database (regulation_enforcement).

Source: https://data.opensanctions.org/datasets/latest/default/ — the
deduplicated OpenSanctions dataset. Crypto-relevant entities are FollowTheMoney
``CryptoWallet`` records inside ``entities.ftm.json``, which is multi-GB and
**line-delimited JSON** (one entity per line, verified live). This source
streams it, keeps only ``CryptoWallet`` entities bounded by
``limit``/``DEFAULT_CAP``, and emits one ``IngestDoc`` each into
``regulation_enforcement`` (doc_type ``sanction_entity``). Fail-open;
``doc_id`` is ``opensanctions:<entity_id>`` (stable, so re-ingestion is
idempotent).

This lane stores the ADDRESS ONLY. The owner's name is joined one entity away
via ``properties.holder`` — see ``opensanctions_holders``, which resolves it
into the separate ``sanction_identities`` collection.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from rugbrain.ingest_sources import licenses
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


def ftm_props(entity: dict[str, Any]) -> dict[str, Any]:
    """FTM properties as a dict (never None). Shared with ``opensanctions_holders``."""
    raw = entity.get("properties")
    return raw if isinstance(raw, dict) else {}


def ftm_clean(values: Any, cap: int) -> list[str]:
    """Dedupe FTM string-lists, drop blanks, cap length. Shared with the holders lane."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values if isinstance(values, list) else []:
        val = str(raw or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
        if len(out) >= cap:
            break
    return out


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

    # NO DERIVATIVES (CC BY-NC-ND): the record is stored VERBATIM. A templated
    # sentence would be an adapted work and therefore a licence breach, so
    # structured fields carry the same facts without rewriting the source text.
    content = str(entity.get("caption") or entity.get("name") or "").strip()
    if not content:
        content = address or entity_id

    metadata = {
        "collection": DEFAULT_COLLECTION,
        **licenses.terms_for("opensanctions").payload_fields(),
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
        content=content + "\n",
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
    "ftm_clean",
    "ftm_props",
    "wallet_entity_to_doc",
]

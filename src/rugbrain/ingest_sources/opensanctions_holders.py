"""OpenSanctions holder resolution — wallet -> sanctioned person/org identity.

``opensanctions.py`` ingests the ``CryptoWallet`` entities and stores the
address string, so every record answers "is this address sanctioned?" and
none can answer "who owns this sanctioned wallet?" — the owner's name is
never joined in, so it is absent at ANY retrieval quality.

The names ARE in the export, one entity away. A wallet's
``properties.holder`` holds a FollowTheMoney entity id (e.g.
``NK-5vaKAsud8hFMsyUSjZCv8r``) that resolves to a real ``Person``/``Company``
carrying ``caption``, ``name[]`` and ``alias[]``:

    us_ofac_sdn  NK-5vaKAsud8hFMsyUSjZCv8r -> Person  "Behrouz Parsarad"
    fbi_lazarus  NK-Xv8CnM8sgddxx7QenotGtb -> Company "Lazarus Group"

``referents`` is NOT the join key and is empty on all 14,042 wallets
(measured 2026-09-29) — ``holder`` is. That distinction is the whole bug.

MEASURED (export 20260929005301-jza, 14,042 wallets): 2,081 (14.8%) resolve to
a named holder — us_ofac_sdn 1,058/1,058, fbi_lazarus 33/33, jp_mof 26/26,
fr_tresor 6/6, il_mod_crypto 958 (exchange accounts) — and all 2,081 emit a
doc (1,177 on-chain + 904 exchange accounts). The 11,186 ``ransomwhere``
wallets are ransom payment addresses with no holder upstream, so no name
exists to join; that gap is upstream, not ours. Full per-dataset counts and
the verbatim fixtures live in ``tests/test_opensanctions_holders.py``.

DESIGN: two bounded passes (index named entities, re-stream, emit) so the
2.6 GB export is never held in memory. ADDS a collection, rewrites none —
``sanction_identities`` is new and the existing 5,013
``regulation_enforcement`` rows keep working untouched. Verbatim-only, same
CC BY-NC-ND constraint as ``opensanctions.py``. Licence is stamped on every
point via ``licenses.terms_for``: the operator ruled this corpus INTERNAL and
never shipped, so a CC-BY-NC / registration-gated source is usable — it is
still recorded because the day it feeds a shipped product you must know what
was never cleared for that.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from rugbrain.ingest_sources import licenses
from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.opensanctions import (
    ENTITIES_URL,
    _ftm_first,
    _iter_lines,
)
from rugbrain.ingest_sources.opensanctions import (
    ftm_clean as _clean,
)
from rugbrain.ingest_sources.opensanctions import (
    ftm_props as _props,
)

log = logging.getLogger(__name__)

#: New collection. Additive — nothing existing is re-pointed or re-dimmed.
DEFAULT_COLLECTION = "sanction_identities"
DEFAULT_CAP = 5_000
SOURCE_URL = "https://opensanctions.org/"

#: Entity schemas that may own a wallet. Anything else is not an identity.
IDENTITY_SCHEMAS = frozenset({"Person", "Company", "Organization", "LegalEntity"})

#: Caps bound one prolific entity so it cannot dominate a point's payload.
MAX_ALIASES, MAX_NAMES, MAX_HOLDERS = 12, 6, 4


async def _entities(
    client: httpx.AsyncClient, url: str, secs: float
) -> AsyncIterator[dict[str, Any]]:
    """Stream the export's JSON Lines, yielding only well-formed entity objects."""
    async for line in _iter_lines(client, url, secs):
        line = line.strip()
        if not line or line in ("[", "]", ","):
            continue
        try:
            entity = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entity, dict):
            yield entity


async def build_identity_index(
    entities: AsyncIterator[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Pass 1 — map holder entity id -> the identity fields a doc needs.

    Keeps ONLY entities that can act as an identity (schema in
    :data:`IDENTITY_SCHEMAS` and a caption or names); every other entity is
    discarded, so peak memory is the identity index alone.
    """
    index: dict[str, dict[str, Any]] = {}
    async for entity in entities:
        if entity.get("schema") not in IDENTITY_SCHEMAS:
            continue
        entity_id = str(entity.get("id") or "").strip()
        if not entity_id:
            continue
        props = _props(entity)
        names = _clean(props.get("name"), MAX_NAMES)
        caption = str(entity.get("caption") or "").strip()
        if not names and not caption:
            continue
        index[entity_id] = {
            "caption": caption,
            "name": names,
            "alias": _clean(props.get("alias"), MAX_ALIASES),
            "entity_type": str(entity.get("schema") or ""),
            "birth_date": _ftm_first(props, "birthDate"),
            "nationality": _clean(props.get("country"), 3),
        }
    return index


def wallet_to_identity_doc(
    entity: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> IngestDoc | None:
    """Join one CryptoWallet to its holder(s); ``None`` when no holder resolves.

    The wallet's own caption is the address and carries no name — the owner
    name comes exclusively from the resolved holder entity.

    Two wallet shapes exist in the export, and both are handled. **on-chain**
    (``publicKey``/``address``) is the normal case. **exchange account**
    (``accountId``, optionally ``managingExchange``) covers 988 Israeli
    ``il_mod_crypto`` records naming a Binance account, not a blockchain
    address; they are emitted too, but typed ``entity_type="exchange_account"``
    and never in the ``address`` field, because filing an exchange account
    number as an address would be false.
    """
    entity_id = str(entity.get("id") or "").strip()
    if not entity_id:
        return None
    props = _props(entity)
    address = _ftm_first(props, "address") or _ftm_first(props, "publicKey")
    exchange = _ftm_first(props, "managingExchange")
    account_id = _ftm_first(props, "accountId")
    if not address and not account_id:
        return None

    holder_ids = [str(h).strip() for h in (props.get("holder") or []) if str(h).strip()]
    resolved = [index[h] for h in holder_ids[:MAX_HOLDERS] if h in index]
    if not resolved:
        return None
    primary = resolved[0]
    names = [n for ident in resolved for n in ident["name"]][:MAX_NAMES]
    aliases = [a for ident in resolved for a in ident["alias"]][:MAX_ALIASES]
    owner_name = primary["caption"] or (names[0] if names else "")
    if not owner_name:
        return None

    raw = entity.get("datasets")
    datasets = [str(d) for d in raw if str(d)] if isinstance(raw, list) else []
    target = bool(entity.get("target"))
    currency = _ftm_first(props, "currency")
    subject = (
        f"Wallet address: {address}" if address else f"Exchange account: {exchange} {account_id}"
    )

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        **licenses.terms_for("opensanctions").payload_fields(),
        "entity_type": "address" if address else "exchange_account",
        "knowledge_type": "sanction_identity",
        "doc_type": "sanction_identity",
        "category": "sanctions",
        "source_url": SOURCE_URL,
        "ecosystem": "multi",
        "severity": "critical" if target else "medium",
        "labels": ["sanctioned", "identity_resolved"]
        if target
        else ["watchlist", "identity_resolved"],
        "entity": address or account_id,
        "address": address,
        "name": owner_name,
        "owner_name": owner_name,
        "owner_entity_type": primary["entity_type"],
        "owner_entity_id": holder_ids[0],
        "owner_names": names,
        "aliases": aliases,
        "holder_ids": holder_ids[:MAX_HOLDERS],
        "wallet_datasets": datasets,
        "has_identity": True,
        # Optional, omitted when the source has no value for them.
        "exchange": exchange,
        "account_id": account_id,
        "currency": currency,
        "birth_date": primary.get("birth_date", ""),
        "nationality": primary.get("nationality", []),
    }
    metadata = {k: v for k, v in metadata.items() if v not in ("", [], None)}
    metadata["address"] = address  # always present: "" means "not an on-chain address"
    if len(resolved) > 1:
        metadata["co_holders"] = [r["caption"] for r in resolved[1:] if r["caption"]][:3]

    # Verbatim, not paraphrased (CC BY-NC-ND) — source field values only.
    content = (
        f"Sanctioned crypto wallet owned by {owner_name}.\n{subject}\n"
        f"Owner entity type: {primary['entity_type']}\n"
        + (f"Currency: {currency}\n" if currency else "")
        + (f"Aliases: {'; '.join(aliases[:6])}\n" if aliases else "")
        + (f"Source datasets: {'; '.join(datasets)}\n" if datasets else "")
    )
    return IngestDoc(
        content=content,
        doc_id=f"opensanctions_identity:{entity_id}",
        metadata=metadata,
        doc_type="sanction_identity",
        source="opensanctions_holders",
        title=f"Sanctioned wallet owned by {owner_name} — {address or f'{exchange} {account_id}'}",
        url=SOURCE_URL,
    )


class OpenSanctionsHolderSource(Source):
    """OpenSanctions crypto wallets JOINED to their named holder entity.

    Two passes over ``entities.ftm.json``: the first builds a holder-identity
    index, the second emits one ``IngestDoc`` per wallet whose holder resolved,
    into ``sanction_identities``. Wallets with no resolvable holder emit
    nothing — an address with no known owner stays address-only, which is
    the truth.
    """

    name = "opensanctions_holders"
    doc_type = "sanction_identity"
    default_collection = DEFAULT_COLLECTION
    description = (
        "OpenSanctions crypto wallets resolved to their named holder (sanction_identities)"
    )
    timeout = 600.0

    def __init__(self, entities_url: str = "") -> None:
        # Overridable so a pinned artifact can be used instead of ``latest``.
        self.entities_url = entities_url or ENTITIES_URL

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            index = await build_identity_index(_entities(client, self.entities_url, self.timeout))
        except Exception as e:  # fail-open, consistent with base.py
            log.warning("opensanctions_holders_index_error err=%s", str(e)[:200])
            return []
        if not index:
            log.warning("opensanctions_holders_index_empty (no named holder entities)")
            return []
        log.info("opensanctions_holders_index_ok identities=%d", len(index))

        docs: list[IngestDoc] = []
        try:
            async for entity in _entities(client, self.entities_url, self.timeout):
                if entity.get("schema") != "CryptoWallet":
                    continue
                doc = wallet_to_identity_doc(entity, index)
                if doc:
                    docs.append(doc)
                    if len(docs) >= cap:
                        break
        except Exception as e:
            log.warning("opensanctions_holders_fetch_error err=%s", str(e)[:200])
            return []
        if not docs:
            log.warning("opensanctions_holders_fetch_empty (no wallet resolved to a named holder)")
        else:
            log.info("opensanctions_holders_feed_ok resolved=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "IDENTITY_SCHEMAS",
    "OpenSanctionsHolderSource",
    "build_identity_index",
    "wallet_to_identity_doc",
]

"""OpenSanctions holder resolution — wallet -> sanctioned person/org identity.

WHY THIS EXISTS
---------------
``opensanctions.py`` ingests the ``CryptoWallet`` entities and stores the
address string. Every stored record therefore answers *"is this address
sanctioned?"* and cannot answer *"who owns this sanctioned wallet?"* — the
owner's name is never joined in, so it is absent at any retrieval quality.

The names ARE in the export, one entity away. A wallet's
``properties.holder`` holds a FollowTheMoney entity id (e.g.
``NK-5vaKAsud8hFMsyUSjZCv8r``) that resolves to a real ``Person``/``Company``
entity carrying ``caption``, ``name[]`` and ``alias[]``:

    us_ofac_sdn  NK-5vaKAsud8hFMsyUSjZCv8r -> Person  "Behrouz Parsarad"
    fbi_lazarus  NK-Xv8CnM8sgddxx7QenotGtb -> Company "Lazarus Group"

``referents`` is NOT the join key and is empty on all 14,042 wallets
(measured 2026-09-29) — ``holder`` is. That distinction is the whole bug.

MEASURED COVERAGE (export 20260929005301-jza, 14,042 CryptoWallet entities)
    holder id resolves to a named entity .... 2,081  (14.8%)
      us_ofac_sdn ......................... 1,058 / 1,058  (100%)
      us_fbi_lazarus_crypto ................    33 /    33  (100%)
      jp_mof_sanctions ......................    26 /    26  (100%)
      fr_tresor_gels_avoir ..................     6 /     6  (100%)
      il_mod_crypto .........................   958  (of which 904 are exchange accounts)
      ransomwhere ...........................     0 / 11,186  (0%)
    emitted as docs .......................... 2,081
      on-chain (entity_type=address) ......... 1,177
      exchange account (entity_type=...) ......   904
The 11,186 ``ransomwhere`` wallets are *ransom payment addresses* — upstream
publishes no holder, so no name exists to join. That gap is upstream, not ours.

DESIGN
------
  * TWO-PASS over the stream, bounded memory. Pass 1 keeps an id -> identity
    index of the named entities only; pass 2 re-streams and emits docs. The
    export is 2.6 GB — it is never held in memory.
  * ADDS a collection, rewrites none. ``sanction_identities`` is new; the
    existing 5,013 ``regulation_enforcement`` rows keep working untouched.
  * Verbatim-only. Same CC BY-NC-ND constraint as ``opensanctions.py``: the
    record is assembled from source field values, never paraphrased.
  * Licence is stamped on every point via ``licenses.terms_for``.

Licence note: the operator ruled this corpus INTERNAL and never shipped, so a
CC-BY-NC / registration-gated source is usable. It is still recorded per point
because the day it feeds a shipped product you must know what was never
cleared for that.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from rugbrain.ingest_sources import licenses
from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.opensanctions import _ftm_first, _iter_lines

log = logging.getLogger(__name__)

#: New collection. Additive — nothing existing is re-pointed or re-dimmed.
DEFAULT_COLLECTION = "sanction_identities"
DEFAULT_CAP = 5_000

#: Entity schemas that may own a wallet. Anything else is not an identity.
IDENTITY_SCHEMAS = frozenset({"Person", "Company", "Organization", "LegalEntity"})

#: Cap on stored aliases per wallet — enough to answer "who is this also
#: known as", bounded so one entity cannot dominate a point's payload.
MAX_ALIASES = 12
MAX_NAMES = 6
MAX_HOLDERS = 4

SOURCE_URL = "https://opensanctions.org/"


def _clean(values: Any, cap: int) -> list[str]:
    """Deduplicate FTM string-list values, drop blanks, cap the length."""
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


async def build_identity_index(
    line_iter: AsyncIterator[str],
) -> dict[str, dict[str, Any]]:
    """Pass 1 — map holder entity id -> the identity fields a doc needs.

    Streams ``entities.ftm.json`` and keeps ONLY entities that can act as an
    identity (schema in :data:`IDENTITY_SCHEMAS` and a caption or names). Every
    other entity is discarded, so peak memory is the identity index alone.
    """
    index: dict[str, dict[str, Any]] = {}
    async for line in line_iter:
        line = line.strip()
        if not line or line in ("[", "]", ","):
            continue
        try:
            entity = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entity, dict) or entity.get("schema") not in IDENTITY_SCHEMAS:
            continue
        entity_id = str(entity.get("id") or "").strip()
        if not entity_id:
            continue
        props = entity.get("properties") if isinstance(entity.get("properties"), dict) else {}
        names = _clean(props.get("name"), MAX_NAMES)  # type: ignore[arg-type]
        caption = str(entity.get("caption") or "").strip()
        if not names and not caption:
            continue
        index[entity_id] = {
            "caption": caption,
            "name": names,
            "alias": _clean(props.get("alias"), MAX_ALIASES),  # type: ignore[arg-type]
            "entity_type": str(entity.get("schema") or ""),
            "birth_date": _ftm_first(props, "birthDate"),  # type: ignore[arg-type]
            "nationality": _clean(props.get("country"), 3),  # type: ignore[arg-type]
        }
    return index


def wallet_to_identity_doc(
    entity: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> IngestDoc | None:
    """Join one CryptoWallet to its holder(s); ``None`` when no holder resolves.

    The wallet's own caption is the address and carries no name — the owner
    name comes exclusively from the resolved holder entity.

    Two wallet shapes are handled, because the export has two:

      * **on-chain** (``publicKey``/``address``) — the normal case.
      * **exchange account** (``managingExchange`` + ``accountId``) — 904
        Israeli ``il_mod_crypto`` records naming a Binance account, not a
        blockchain address. They are emitted too, but typed
        ``entity_type="exchange_account"`` and never in the ``address`` field:
        filing an exchange account number as an address would be false.
    """
    entity_id = str(entity.get("id") or "").strip()
    if not entity_id:
        return None
    props = entity.get("properties") if isinstance(entity.get("properties"), dict) else {}
    address = _ftm_first(props, "address") or _ftm_first(props, "publicKey")  # type: ignore[arg-type]
    exchange = _ftm_first(props, "managingExchange")  # type: ignore[arg-type]
    account_id = _ftm_first(props, "accountId")  # type: ignore[arg-type]
    if not address and not (exchange and account_id):
        return None

    holder_ids = [str(h).strip() for h in (props.get("holder") or []) if str(h).strip()]  # type: ignore[arg-type]
    resolved = [index[h] for h in holder_ids[:MAX_HOLDERS] if h in index]
    if not resolved:
        return None

    primary = resolved[0]
    names = [n for ident in resolved for n in (ident["name"] or [])][:MAX_NAMES]
    aliases = [a for ident in resolved for a in (ident["alias"] or [])][:MAX_ALIASES]
    # The caption is the canonical display name; fall back to name[0].
    owner_name = primary["caption"] or (names[0] if names else "")
    if not owner_name:
        return None

    raw_datasets = entity.get("datasets")
    datasets = [str(d) for d in raw_datasets if str(d)] if isinstance(raw_datasets, list) else []
    target = bool(entity.get("target"))
    currency = _ftm_first(props, "currency")  # type: ignore[arg-type]
    entity_type = "address" if address else "exchange_account"

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        **licenses.terms_for("opensanctions").payload_fields(),
        "entity_type": entity_type,
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
        "owner_entity_id": holder_ids[0] if holder_ids else "",
        "owner_names": names,
        "aliases": aliases,
        "holder_ids": holder_ids[:MAX_HOLDERS],
        "wallet_datasets": datasets,
        "has_identity": True,
    }
    if currency:
        metadata["currency"] = currency
    if exchange:
        metadata["exchange"] = exchange
    if account_id:
        metadata["account_id"] = account_id
    if primary.get("birth_date"):
        metadata["birth_date"] = primary["birth_date"]
    if primary.get("nationality"):
        metadata["nationality"] = primary["nationality"]
    if len(resolved) > 1:
        metadata["co_holders"] = [r["caption"] for r in resolved[1:] if r["caption"]][:3]

    subject = (
        f"Wallet address: {address}" if address else f"Exchange account: {exchange} {account_id}"
    )
    # Verbatim, not paraphrased (CC BY-NC-ND). Source field values only.
    content = (
        f"Sanctioned crypto wallet owned by {owner_name}.\n"
        f"{subject}\n"
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
    into ``sanction_identities`` (doc_type ``sanction_identity``). Wallets
    with no resolvable holder emit nothing — an address with no known owner
    stays address-only, which is the truth.
    """

    name = "opensanctions_holders"
    doc_type = "sanction_identity"
    default_collection = DEFAULT_COLLECTION
    description = (
        "OpenSanctions crypto wallets resolved to their named holder entity (sanction_identities)"
    )
    timeout = 600.0

    def __init__(self, entities_url: str = "") -> None:
        # Overridable so a pinned artifact can be used instead of ``latest``.
        self.entities_url = entities_url or _default_entities_url()

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            index = await build_identity_index(_iter_lines(client, self.entities_url, self.timeout))
        except Exception as e:  # fail-open, consistent with base.py
            log.warning("opensanctions_holders_index_error err=%s", str(e)[:200])
            return []
        if not index:
            log.warning("opensanctions_holders_index_empty (no named holder entities)")
            return []
        log.info("opensanctions_holders_index_ok identities=%d", len(index))

        docs: list[IngestDoc] = []
        try:
            async for line in _iter_lines(client, self.entities_url, self.timeout):
                line = line.strip()
                if not line or line in ("[", "]", ","):
                    continue
                try:
                    entity = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entity, dict) or entity.get("schema") != "CryptoWallet":
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


def _default_entities_url() -> str:
    """Reuse the sibling module's URL so both lanes track the same export."""
    from rugbrain.ingest_sources.opensanctions import ENTITIES_URL

    return ENTITIES_URL


__all__ = [
    "DEFAULT_COLLECTION",
    "IDENTITY_SCHEMAS",
    "OpenSanctionsHolderSource",
    "build_identity_index",
    "wallet_to_identity_doc",
]

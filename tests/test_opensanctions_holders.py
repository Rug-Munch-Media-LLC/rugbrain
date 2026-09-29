"""Tests for the OpenSanctions holder-join module.

Fixtures are the REAL records measured from export ``20260929005301-jza``,
so a regression in the join logic is caught against the shape upstream
actually ships rather than an invented one.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from rugbrain.ingest_sources.opensanctions_holders import (
    DEFAULT_COLLECTION,
    IDENTITY_SCHEMAS,
    build_identity_index,
    wallet_to_identity_doc,
)

# --- REAL fixtures (verbatim from entities.ftm.json, 20260929005301-jza) ---

PERSON_ENTITY = {
    "id": "NK-5vaKAsud8hFMsyUSjZCv8r",
    "caption": "Behrouz Parsarad",
    "schema": "Person",
    "datasets": ["us_ofac_sdn"],
    "target": True,
    "referents": [],
    "properties": {
        "name": ["PARSARAD, Behrouz", "Behrouz Parsarad", "بهروز پارسا راد"],
        "alias": ["Behrouz Parsa", "بهروز پارسا"],
        "birthDate": ["1988-07-02"],
        "country": ["ir"],
        "gender": ["male"],
    },
}

COMPANY_ENTITY = {
    "id": "NK-Xv8CnM8sgddxx7QenotGtb",
    "caption": "Lazarus Group",
    "schema": "Company",
    "datasets": ["us_fbi_lazarus_crypto"],
    "target": True,
    "referents": [],
    "properties": {
        "alias": ["Silent Chollima", "Guardians of Peace", "Onyx Sleet", "APT 38"],
        "name": ["Lazarus Group"],
        "country": ["kp"],
    },
}

# referents is [] on all 14,042 wallets — the join key is holder, not referents.
OFAC_WALLET = {
    "id": "NK-6UWgMcHA6RoYuCzWThkEMQ",
    "caption": "bc1qcp6fr7gtyukympl6unr7uv78h3vprycwj455zx",
    "schema": "CryptoWallet",
    "referents": [],
    "datasets": ["us_ofac_sdn"],
    "target": True,
    "properties": {
        "holder": ["NK-5vaKAsud8hFMsyUSjZCv8r"],
        "publicKey": ["bc1qcp6fr7gtyukympl6unr7uv78h3vprycwj455zx"],
        "currency": ["XBT"],
        "topics": ["crime.fin"],
    },
}

FBI_WALLET = {
    "id": "fbi-lazarus-16600ec2ce87bbe1d621bc8a49126ddb941ae80",
    "caption": "bc1qqvpjgaurtnhc8smkmdtwhx9c8207m0prsyxyjx",
    "schema": "CryptoWallet",
    "referents": [],
    "datasets": ["us_fbi_lazarus_crypto"],
    "target": True,
    "properties": {
        "holder": ["NK-Xv8CnM8sgddxx7QenotGtb"],
        "publicKey": ["bc1qqvpjgaurtnhc8smkmdtwhx9c8207m0prsyxyjx"],
        "currency": ["Bitcoin"],
        "topics": ["crime.fin", "crime.cyber"],
    },
}

# 904 il_mod_crypto wallets are exchange accounts, not addresses.
EXCHANGE_WALLET = {
    "id": "il-nbctf-0020f29b0a7ee2dc1406624c4b939455df751892",
    "caption": "Cryptocurrency wallet",
    "schema": "CryptoWallet",
    "referents": [],
    "datasets": ["il_mod_crypto"],
    "target": True,
    "properties": {
        "managingExchange": ["Binance"],
        "accountId": ["112604404"],
        "holder": ["NK-XnzZYvRUgstJ24NBe8zn3g"],
        "topics": ["crime.terror"],
    },
}

# 84 of them carry accountId with no managingExchange.
ACCOUNT_ONLY_WALLET = {
    "id": "il-nbctf-03c9857ab64ef8e8f6c13563b02ee169fa52376f",
    "caption": "Cryptocurrency wallet",
    "schema": "CryptoWallet",
    "referents": [],
    "datasets": ["il_mod_crypto"],
    "target": True,
    "properties": {
        "accountId": ["426445650"],
        "holder": ["NK-XnzZYvRUgstJ24NBe8zn3g"],
        "topics": ["crime.terror"],
    },
}

RANSOM_WALLET = {
    "id": "rwre-15ajjv1rcfdmmnhkrak4zduh6z4lnredif",
    "caption": "15aJJV1RCFdmMNhkRaK4zdUH6Z4LnrEdiF",
    "schema": "CryptoWallet",
    "referents": [],
    "datasets": ["ransomwhere"],
    "target": True,
    "properties": {
        "publicKey": ["15aJJV1RCFdmMNhkRaK4zdUH6Z4LnrEdiF"],
        "currency": ["Bitcoin"],
    },
}


def _lines(*entities: dict[str, Any]) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        for ent in entities:
            yield json.dumps(ent, ensure_ascii=False)

    return gen()


async def _index(*entities: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return await build_identity_index(_lines(*entities))


# --- the core defect: a wallet with NO holder yields NO doc ---------------


@pytest.mark.asyncio
async def test_ransomwhere_wallet_without_holder_emits_nothing() -> None:
    """The 11,186 ransomware wallets have no owner upstream — no name invented."""
    idx = await _index()
    assert wallet_to_identity_doc(RANSOM_WALLET, idx) is None


@pytest.mark.asyncio
async def test_unknown_holder_id_emits_nothing() -> None:
    """A holder id absent from the export must not produce a doc."""
    idx = await _index(PERSON_ENTITY)
    orphan = {
        **OFAC_WALLET,
        "properties": {**OFAC_WALLET["properties"], "holder": ["NK-doesnotexist"]},
    }
    assert wallet_to_identity_doc(orphan, idx) is None


# --- the fix: name is joined from the holder entity -----------------------


@pytest.mark.asyncio
async def test_ofac_wallet_resolves_to_named_person() -> None:
    idx = await _index(PERSON_ENTITY)
    doc = wallet_to_identity_doc(OFAC_WALLET, idx)
    assert doc is not None
    assert doc.metadata["owner_name"] == "Behrouz Parsarad"
    assert doc.metadata["owner_entity_type"] == "Person"
    assert doc.metadata["name"] == "Behrouz Parsarad"
    assert "Behrouz Parsarad" in doc.content
    assert doc.metadata["birth_date"] == "1988-07-02"
    assert doc.metadata["nationality"] == ["ir"]
    assert doc.metadata["entity_type"] == "address"
    assert doc.metadata["address"] == "bc1qcp6fr7gtyukympl6unr7uv78h3vprycwj455zx"


@pytest.mark.asyncio
async def test_company_wallet_resolves_with_aliases() -> None:
    idx = await _index(COMPANY_ENTITY)
    doc = wallet_to_identity_doc(FBI_WALLET, idx)
    assert doc is not None
    assert doc.metadata["owner_name"] == "Lazarus Group"
    assert "Silent Chollima" in doc.metadata["aliases"]
    assert "Guardians of Peace" in doc.content


@pytest.mark.asyncio
async def test_join_key_is_holder_not_referents() -> None:
    """``referents`` is [] on every wallet; holder is the only join key."""
    assert OFAC_WALLET["referents"] == []
    idx = await _index(PERSON_ENTITY)
    assert wallet_to_identity_doc(OFAC_WALLET, idx) is not None


# --- the exchange-account shape must not be filed as an address ---------


@pytest.mark.asyncio
async def test_exchange_account_is_typed_and_not_an_address() -> None:
    idx = await _index(
        {
            "id": "NK-XnzZYvRUgstJ24NBe8zn3g",
            "caption": "Al-Markaziya Li-Siarafa",
            "schema": "Company",
            "properties": {"name": ["Al-Markaziya Li-Siarafa"]},
        }
    )
    doc = wallet_to_identity_doc(EXCHANGE_WALLET, idx)
    assert doc is not None
    assert doc.metadata["entity_type"] == "exchange_account"
    assert doc.metadata["address"] == ""
    assert doc.metadata["account_id"] == "112604404"
    assert doc.metadata["exchange"] == "Binance"
    assert "Binance 112604404" in doc.content


@pytest.mark.asyncio
async def test_account_id_only_wallet_still_resolves() -> None:
    """84 records carry accountId with no managingExchange — still attributable."""
    idx = await _index(
        {
            "id": "NK-XnzZYvRUgstJ24NBe8zn3g",
            "caption": "Al-Markaziya Li-Siarafa",
            "schema": "Company",
            "properties": {"name": ["Al-Markaziya Li-Siarafa"]},
        }
    )
    doc = wallet_to_identity_doc(ACCOUNT_ONLY_WALLET, idx)
    assert doc is not None
    assert doc.metadata["entity_type"] == "exchange_account"
    assert doc.metadata["account_id"] == "426445650"


# --- structural guarantees ----------------------------------------------


@pytest.mark.asyncio
async def test_doc_writes_only_the_new_collection() -> None:
    """One module, one collection — never regulation_enforcement."""
    idx = await _index(PERSON_ENTITY, COMPANY_ENTITY)
    for wallet in (OFAC_WALLET, FBI_WALLET):
        doc = wallet_to_identity_doc(wallet, idx)
        assert doc is not None
        assert doc.metadata["collection"] == DEFAULT_COLLECTION
        assert DEFAULT_COLLECTION != "regulation_enforcement"


@pytest.mark.asyncio
async def test_licence_is_stamped_on_every_doc() -> None:
    idx = await _index(PERSON_ENTITY, COMPANY_ENTITY)
    for wallet in (OFAC_WALLET, FBI_WALLET):
        doc = wallet_to_identity_doc(wallet, idx)
        assert doc is not None
        assert doc.metadata["license"] == "CC BY-NC-ND 4.0"
        assert doc.metadata["source_id"] == "opensanctions"
        assert doc.metadata["redistribute"] is False


@pytest.mark.asyncio
async def test_doc_id_is_deterministic_and_keyed_on_entity() -> None:
    idx = await _index(PERSON_ENTITY)
    first = wallet_to_identity_doc(OFAC_WALLET, idx)
    second = wallet_to_identity_doc(OFAC_WALLET, idx)
    assert first is not None and second is not None
    assert first.doc_id == second.doc_id == f"opensanctions_identity:{OFAC_WALLET['id']}"


@pytest.mark.asyncio
async def test_index_ignores_non_identity_schemas() -> None:
    """Only Person/Company/Organization/LegalEntity may act as an identity."""
    idx = await _index(
        {
            "id": "X-1",
            "caption": "Some Address",
            "schema": "CryptoWallet",
            "properties": {"publicKey": ["abc"]},
        }
    )
    assert "X-1" not in idx
    assert {"Person", "Company", "Organization", "LegalEntity"} == IDENTITY_SCHEMAS


@pytest.mark.asyncio
async def test_index_skips_blank_and_malformed_lines() -> None:
    async def gen() -> AsyncIterator[str]:
        yield ""
        yield "["
        yield "{not json"
        yield json.dumps(PERSON_ENTITY)

    idx = await build_identity_index(gen())
    assert "NK-5vaKAsud8hFMsyUSjZCv8r" in idx


@pytest.mark.asyncio
async def test_wallet_without_id_emits_nothing() -> None:
    idx = await _index(PERSON_ENTITY)
    assert wallet_to_identity_doc({**OFAC_WALLET, "id": ""}, idx) is None

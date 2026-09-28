"""Licence gate tests: the registry must default to the SAFEST permitted use."""

from __future__ import annotations

from rugbrain.ingest_sources import licenses
from rugbrain.ingest_sources.opensanctions import wallet_entity_to_doc


def test_opensanctions_is_not_chunkable_nd() -> None:
    """CC BY-NC-ND forbids derivatives, so its records must never be chunked."""
    assert licenses.terms_for("opensanctions").license == "CC BY-NC-ND 4.0"
    assert licenses.may_chunk("opensanctions") is False
    assert licenses.may_redistribute("opensanctions") is False
    assert licenses.is_research_only("opensanctions") is True


def test_undeclared_source_defaults_to_safest() -> None:
    """A new source is protected until someone explicitly widens it."""
    assert licenses.may_chunk("a_brand_new_feed") is False
    assert licenses.is_research_only("a_brand_new_feed") is True
    assert licenses.terms_for("a_brand_new_feed").license == "UNDECLARED"


def test_ofac_is_public_domain() -> None:
    assert licenses.may_chunk("ofac_sdn") is True


def test_stored_content_is_verbatim_not_derived() -> None:
    """The old build templated a sentence; that is an adapted work under ND."""
    entity = {
        "id": "NK-abc123",
        "caption": "Acme Exchange",
        "datasets": ["ofac_sdn"],
        "target": True,
        "properties": {"address": "15aJJV1RCFdmMNhkRaK4zdUH6Z4LnrEdiF", "asset": "XMR"},
    }
    doc = wallet_entity_to_doc(entity)
    assert doc is not None
    assert doc.content.strip() == "Acme Exchange"
    assert "OpenSanctions crypto wallet" not in doc.content
    assert "Source datasets" not in doc.content
    # The facts are preserved as structured fields instead of prose.
    assert doc.metadata["address"] == "15aJJV1RCFdmMNhkRaK4zdUH6Z4LnrEdiF"
    assert doc.metadata["labels"] == ["sanctioned"]


def test_every_point_carries_its_licence_for_audit() -> None:
    entity = {"id": "NK-1", "properties": {"address": "0xabc"}}
    doc = wallet_entity_to_doc(entity)
    assert doc is not None
    for field in ("license", "permitted_use", "chunkable", "redistribute"):
        assert field in doc.metadata

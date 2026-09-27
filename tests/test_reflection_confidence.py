"""Regression: reflection must not discount a query that names no address.

Baseline (recorded 2026-09-27, before the fix) from ``/api/v1/search``:

    q="recent exploits on Base"  collection=defi_exploits
    reflection.refs_found.eth_addresses = []
    reflection.verified_refs         = []
    reflection.confidence_multiplier = 0.5     <-- wrong

Every such retrieval question reported 0.5, which reads as "half the
answer is unverified" when in fact there is nothing on-chain to verify.
Q12 (an address-bearing query) legitimately reported 1.0 because its
address did verify — so the multiplier was already 1.0 in the correct
case, and the 0.5 case was the defect.

The rule: a penalty is warranted only when the query NAMED an address and
the RPC could not confirm it.
"""

from __future__ import annotations

import pytest

from rugbrain.pre_generation_reflection import pre_generation_reflection
from rugbrain.reflection_confidence import score_confidence


@pytest.mark.asyncio
async def test_retrieval_query_gets_neutral_confidence() -> None:
    """Q01 — a named-protocol retrieval question, no address in the query."""
    reflection = await pre_generation_reflection("recent exploits on Base")

    assert reflection["refs_found"]["eth_addresses"] == []
    assert reflection["verified_refs"] == []
    assert reflection["block_generation"] is False
    assert reflection["confidence_multiplier"] == 1.0, (
        "a query with no on-chain reference has nothing to verify and must "
        "not be reported as unverified"
    )


@pytest.mark.asyncio
async def test_address_verified_keeps_neutral_confidence() -> None:
    """Q12 shape — a named address that verifies stays at 1.0."""
    reflection = await pre_generation_reflection(
        "has 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 been flagged before"
    )

    assert reflection["refs_found"]["eth_addresses"], "precondition: an address was extracted"
    assert reflection["confidence_multiplier"] == 1.0


def test_unverified_named_address_is_penalised() -> None:
    """The 0.5 penalty survives — for the case that actually earns it."""
    multiplier = score_confidence(
        verified=[],
        discrepancies=[],
        refs={"eth_addresses": ["0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"]},
    )

    assert multiplier == 0.5


def test_discrepancy_blocks() -> None:
    """A RAG-vs-chain conflict still hard-blocks at 0.0."""
    multiplier = score_confidence(
        verified=[{"address": "0xabc", "verified": True}],
        discrepancies=[{"address": "0xabc", "issue": "conflict"}],
        refs={"eth_addresses": ["0xabc"]},
    )

    assert multiplier == 0.0

"""Confidence scoring for pre-generation reflection.

Separated from ``pre_generation_reflection`` (which sits at the 200-line
file cap) so the scoring rule owns its own module and is unit-testable
without touching RPCs.

The rule encodes one distinction that the previous inline expression
conflated:

    "nothing to verify"  is NOT the same as  "failed to verify".

A retrieval question ("recent exploits on Base") references no on-chain
address, so there is no unresolved claim to discount and the multiplier
must be neutral. Reporting 0.5 there mis-signalled every such answer as
half-unverified. A 0.5 multiplier is only warranted when the query *did*
name an address and the RPC could not confirm it.
"""

from __future__ import annotations

from typing import Any

NEUTRAL = 1.0
UNVERIFIED_PENALTY = 0.5
BLOCKED = 0.0


def score_confidence(
    verified: list[dict[str, Any]],
    discrepancies: list[dict[str, Any]],
    refs: dict[str, list[str]],
) -> float:
    """Return the confidence multiplier for a reflection pass.

    Args:
        verified: Addresses successfully confirmed against live RPC.
        discrepancies: RAG-vs-chain conflicts; any entry blocks output.
        refs: On-chain references extracted from the query, as returned by
            ``extract_onchain_refs``.

    Returns:
        ``0.0`` when output is blocked, ``0.5`` when a named address could
        not be verified, otherwise ``1.0``.
    """
    if discrepancies:
        return BLOCKED
    if verified:
        return NEUTRAL
    if refs.get("eth_addresses"):
        return UNVERIFIED_PENALTY
    return NEUTRAL

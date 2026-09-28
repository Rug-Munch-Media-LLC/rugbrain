"""``rug_events`` — the structured rug-pull event store.

The gap this package closes: rug telemetry was trapped in undated prose inside
``exploits`` / ``defi_exploits`` (a row carrying "Total removed liquidity:
2691364.08" with ``date: ""``). Retrieval cannot answer "who rugged, when, how
much" from that. Every module here turns data the repo ALREADY holds into one
row per event with indexed, filterable fields.

Sub-modules:

  * ``schema``    — the payload contract + Qdrant payload index plan.
  * ``solrpds``   — extractor for the Solana rug dataset already in Qdrant.
  * ``deployers`` — ``deployer_prior_rugs`` aggregation.
"""

from __future__ import annotations

__all__ = ["COLLECTION"]

COLLECTION = "rug_events"

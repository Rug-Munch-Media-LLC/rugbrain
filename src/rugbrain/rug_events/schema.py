"""The ``rug_events`` payload contract and its Qdrant payload index plan.

Qdrant is the right store for this: it is a searchable event store whose
filtered queries are served from payload indexes. Without an index every filter
is a full payload scan — measured on this host, a ``news_v2`` category filter
cost 22.8x, ``scam_intel_v2`` chain 29.7x (``scripts/apply_payload_indexes.py``).

Two tiers of index, deliberately:

  * ``KEYWORD`` on low-cardinality identity/routing fields — the filters that
    actually get issued ("all Solana rugs", "all rugs by this deployer").
  * ``FLOAT`` on the economic fields — Qdrant supports float range indexes, so
    "rugs that removed more than $1M" is an index range scan, not a scan.

``rug_ts`` is a ``datetime`` index so "rugs in 2022" is a range filter.

Every field listed here is populated by ``solrpds``/``scythe_radar``/``crpwarner``
whenever the underlying source has it and is left absent otherwise. An absent
field is honest; a fabricated zero is not.
"""

from __future__ import annotations

from typing import Any

COLLECTION = "rug_events"

#: Field path -> Qdrant index schema.
KEYWORD = {"type": "keyword"}
FLOAT = {"type": "float"}
DATETIME = {"type": "datetime"}

#: The single source of truth for what ``rug_events`` looks like.
PAYLOAD_FIELDS: dict[str, str] = {
    "token": "str — token symbol/name",
    "token_address": "str — event identity, one point per (token, year)",
    "chain": "str — solana | ethereum | bsc | multi | unknown",
    "deployer": "str — deployer/creator address; '' when the source has none",
    "rug_ts": "datetime — last pool activity, the observed rug moment",
    "liq_added_usd": "float — total liquidity ever added",
    "liq_removed_usd": "float — total liquidity removed",
    "rug_ratio": "float — removed / added (>1 means more left than entered)",
    "holders_at_rug": "int — holder count at the rug (absent unless sourced)",
    "deployer_prior_rugs": "int — DERIVED: rugs already attributed to this deployer",
    "deployer_attributed": "bool — READ THIS FIRST. False means the deployer is "
    "UNKNOWN, not clean: no source in this corpus publishes a creator for these "
    "rows, so a 0 prior-rug count carries no information. See DEPLOYER_STATUS.",
    "source": "str — provenance key; resolve to a URL via SOURCE_URLS",
    "confidence": "float — 0..1, how directly the row evidences the event",
    "severity": "str — triage",
    "category": "str — triage",
    "rug_mechanism": "str — how it was rugged",
    "rug_outcome": "str — what it became",
    "rug_classification": "str — contract-related vs transaction-related",
    "loss_kusd": "float — documented economic loss, kUSD",
    "early_warning": "str — signal observable before the rug",
    "pool_address": "str — the liquidity pool that was drained",
    "source_url": "str — human-checkable link to the original",
}

#: field -> index schema. Anything not listed is stored but unindexed.
INDEX_PLAN: dict[str, dict[str, Any]] = {
    "token_address": KEYWORD,
    "chain": KEYWORD,
    "deployer": KEYWORD,
    "source": KEYWORD,
    "severity": KEYWORD,
    "category": KEYWORD,
    "rug_mechanism": KEYWORD,
    "rug_outcome": KEYWORD,
    "rug_classification": KEYWORD,
    "rug_ts": DATETIME,
    "liq_added_usd": FLOAT,
    "liq_removed_usd": FLOAT,
    "rug_ratio": FLOAT,
}

#: Provenance key -> the URL a human can open to check the row.
SOURCE_URLS: dict[str, str] = {
    "deflabx_solrpds": "https://github.com/DeFiLabX/SolRPDS",
    "scythe_radar": "https://github.com/scytheradar/scythe-radar-datasets",
    "crpwarner": "https://github.com/CRPWarner/RugPull",
}

#: Required keys. A row missing any of these is not an event and is rejected.
REQUIRED = ("token_address", "chain", "source", "confidence")

#: Why ``deployer_attributed`` is False on every row today. Measured, not
#: assumed — see the lane report of 2026-09-28.
#:
#: The only labelled creator field available offline is Forta's
#: ``contract_creator`` (139,600 rows, fetched and scanned in full). The join
#: was measured against all 1,527 distinct token addresses in this collection
#: and returned **0 matches**. That is a chain mismatch, not a coverage gap:
#:
#:   * 1,508 / 1,527 token addresses are base58 Solana mints. Forta is EVM-only.
#:   * The remaining 19 ARE EVM (all scythe-radar rows) and still miss: scythe
#:     publishes no creator field, and none appear in Forta's contract set.
#:
#: So no row can be attributed from data this repo holds, and none is. The
#: field stays False rather than being filled with a guess.
DEPLOYER_STATUS = (
    "unattributed — no creator published by any in-repo source; "
    "Forta join measured 0/1527 (EVM-only vs 1508 Solana mints)"
)


def validate(row: dict[str, Any]) -> list[str]:
    """Return the names of REQUIRED fields missing from *row* ([] when valid)."""
    return [k for k in REQUIRED if not row.get(k)]


__all__ = [
    "COLLECTION",
    "DATETIME",
    "DEPLOYER_STATUS",
    "FLOAT",
    "INDEX_PLAN",
    "KEYWORD",
    "PAYLOAD_FIELDS",
    "REQUIRED",
    "SOURCE_URLS",
    "validate",
]

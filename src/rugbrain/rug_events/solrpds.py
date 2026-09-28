"""Extract structured rug events from the Solana rug dataset already in Qdrant.

Source of truth: ``DeFiLabX/SolRPDS``, ingested into this repo's own
``defi_exploits`` and ``exploits`` collections. Observed live 2026-09-28:
**1,858 rows in ``defi_exploits`` + 137 in ``exploits``**, each already carrying
a structured ``metadata`` dict (chain, token_address, pool_address,
total_added_liquidity, total_removed_liquidity, add_to_remove_ratio,
first/last_pool_activity, inactivity_status) — plus, in ``exploits`` only, a
flattened prose copy in ``code_block``.

This is REUSE, not acquisition. The data is already paid for, already stored
and already indexed; it was simply never projected into a queryable shape. The
date was the blocker: the source row carried ``date: ""`` while the real
timestamps sat in ``last_pool_activity``, so nothing could be asked "in 2022".

Two shapes are handled because the two collections stored it differently:

  * ``defi_exploits`` — ``payload["metadata"]`` is a real dict. Preferred.
  * ``exploits``       — ``metadata`` is a *stringified* dict and the values
    live in ``code_block``. Handled by the line parser, which is the same one
    the flat shape needs, and is unit-tested against real rows.

``rug_ratio`` is NOT copied from ``add_to_remove_ratio``: that field is
added/removed, so inverted. We want removed/added so that **> 1.0 means more
left the pool than ever entered it** — the actual rug signal.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

SOURCE = "deflabx_solrpds"
SOURCE_URL = "https://github.com/DeFiLabX/SolRPDS"

#: Confidence by evidence strength. The SolRPDS rows are measured on-chain
#: liquidity movement, not a narrative claim, so they sit high; the
#: ``code_block``-only variant loses a notch because it round-trips through
#: prose rather than typed fields.
CONFIDENCE_METADATA = 0.95
CONFIDENCE_CODE_BLOCK = 0.85

_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z /()-]*?):\s*(.+?)\s*$")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

#: The flat ``code_block`` writes its labels as prose; these map them onto the
#: same field names the typed ``metadata`` uses, so ``build_event`` has ONE
#: shape to read. The token label is the awkward one — "Solana rug-pull token".
LABEL_ALIASES: dict[str, str] = {
    "Solana rug-pull token": "token_address",
    "Token": "token_address",
    "Year": "year",
    "Liquidity pool": "pool_address",
    "Total added liquidity": "total_added_liquidity",
    "Total removed liquidity": "total_removed_liquidity",
    "First pool activity": "first_pool_activity",
    "Last pool activity": "last_pool_activity",
    "Status": "inactivity_status",
}


def _num(value: Any) -> float | None:
    """Parse a numeric field; None on anything that is not a plain number."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    return float(text) if _NUM_RE.match(text) else None


def parse_code_block(text: str) -> dict[str, str]:
    """Parse the flat ``Key: value`` block into typed field names.

    Labels are normalized through ``LABEL_ALIASES`` so the result reads like
    the typed ``metadata`` dict the other collection uses. Unmapped labels keep
    their prose name (e.g. ``Add/remove ratio``) — nothing is silently dropped.
    Malformed lines are skipped rather than raising: the corpus is prose-shaped.
    """
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = _LABEL_RE.match(line.strip())
        if m:
            label = m.group(1).strip()
            out[LABEL_ALIASES.get(label, label)] = m.group(2).strip()
    return out


def _coerce_metadata(value: Any) -> dict[str, Any]:
    """Return the metadata as a real dict, parsing it when stored as a string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        # Only reached for the ``exploits`` shape, whose metadata was dumped
        # into a string by an older writer. ``literal_eval`` on our own
        # previously-serialised payload, never on remote input.
        from ast import literal_eval

        try:
            parsed = literal_eval(value)
        except (ValueError, SyntaxError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def build_event(payload: dict[str, Any], point_id: str) -> dict[str, Any] | None:
    """One stored exploit/defi_exploits point -> one ``rug_events`` payload.

    Returns None when the point is not a rug event (no token address), so the
    caller can filter a whole collection without a pre-check.
    """
    metadata = _coerce_metadata(payload.get("metadata"))
    if not metadata:
        # The ``exploits`` shape puts the record in ``code_block`` only.
        metadata = parse_code_block(str(payload.get("code_block") or ""))
        confidence = CONFIDENCE_CODE_BLOCK
    else:
        confidence = CONFIDENCE_METADATA

    token = str(metadata.get("token_address") or "").strip()
    if not token:
        return None

    added = _num(metadata.get("total_added_liquidity"))
    removed = _num(metadata.get("total_removed_liquidity"))
    # add_to_remove_ratio is added/removed; we want the inverse, and only
    # compute it from the raw amounts so it never depends on that inversion.
    rug_ratio = (removed / added) if (added and removed is not None and added > 0) else None

    year = metadata.get("year")
    # Deterministic identity: the source keys its rows by (token, year).
    event_id = f"deflabx_solrpds:{token}:{year}" if year else f"deflabx_solrpds:{token}"

    event: dict[str, Any] = {
        "token_address": token,
        "chain": str(metadata.get("chain") or metadata.get("ecosystem") or "").strip() or "unknown",
        "deployer": str(metadata.get("deployer") or "").strip(),
        "rug_ts": str(metadata.get("last_pool_activity") or "").strip(),
        "deployer_prior_rugs": 0,
        "source": SOURCE,
        "source_url": str(metadata.get("source_url") or SOURCE_URL).strip(),
        "confidence": confidence,
        "severity": "critical",
        "category": "rug-pull",
        "point_id": point_id,
        "event_id": event_id,
    }
    if added is not None:
        event["liq_added_usd"] = added
    if removed is not None:
        event["liq_removed_usd"] = removed
    if rug_ratio is not None:
        event["rug_ratio"] = round(rug_ratio, 6)
    for key, src in (
        ("rug_mechanism", "inactivity_status"),
        ("rug_outcome", "inactivity_status"),
        ("pool_address", "pool_address"),
        ("rug_classification", "rug_classification"),
        ("rug_pattern", "rug_pattern"),
    ):
        value = str(metadata.get(src) or "").strip()
        if value:
            event[key] = value
    if year:
        event["year"] = year
    return event


__all__ = [
    "CONFIDENCE_CODE_BLOCK",
    "CONFIDENCE_METADATA",
    "SOURCE",
    "SOURCE_URL",
    "build_event",
    "parse_code_block",
]

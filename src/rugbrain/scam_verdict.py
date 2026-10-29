"""Verdict rules for the address scam check.

The verdict is a function of **address-scoped** evidence only. Two rules from
the previous implementation are deliberately gone, because each one produced
false positives on blue-chip tokens:

1. ``max_score`` came from *semantic similarity* to the scam corpora. Every
   document in those collections is a scam/threat record by construction, so
   similarity measures topical resemblance to the word "scam" — not evidence
   about the queried address. A generic query ("scam check address 0x… on
   ethereum") retrieves dozens of high-severity rows for *any* input.
2. ``risk_signals >= 2`` counted those rows as two independent findings.
   Two unrelated phishing-domain documents mentioning the queried symbol were
   enough for ``high``.

A label row now counts only when its ``metadata.address`` equals the queried
address. Confidence is an evidence grade, not a vector score, so it can be
justified line by line.
"""

from __future__ import annotations

from typing import Any, Final

from rugbrain.blue_chips import lookup_blue_chip

_HIGH: Final = frozenset({"high", "critical"})
_MEDIUM: Final = frozenset({"medium", "moderate"})
_LOW: Final = frozenset({"low", "info", "informational"})

#: Evidence grade per verdict — fixed, so two callers with the same evidence
#: always see the same confidence. Similarity never enters this table.
_CONFIDENCE: Final[dict[str, int]] = {
    "high": 90,
    "medium": 65,
    "low": 80,
    "unknown": 0,
}


def _severity_of(payload: dict[str, Any]) -> str:
    return str(payload.get("severity", "") or "").strip().lower()


def _is_address_scoped(address: str, payload: dict[str, Any]) -> bool:
    """True when the row's own ``metadata.address`` is the queried address."""
    metadata = payload.get("metadata")
    row_address = ""
    if isinstance(metadata, dict):
        row_address = str(metadata.get("address", "") or "")
    row_address = row_address or str(payload.get("address", "") or "")
    return bool(row_address) and row_address.strip().lower() == address.strip().lower()


def decide_verdict(address: str, hits: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Grade *address* from address-scoped label rows.

    Returns ``scam_risk``, ``confidence`` and ``verdict_reason``. ``unknown``
    means "no address-scoped evidence", which is not the same as clean and must
    never be rendered as a risk score.
    """
    severities = [
        _severity_of(payload)
        for _collection, payload in hits
        if _is_address_scoped(address, payload)
    ]
    if not severities:
        chip = lookup_blue_chip(address)
        if chip is not None:
            return {
                "scam_risk": "low",
                "confidence": _CONFIDENCE["low"],
                "verdict_reason": f"verified_blue_chip:{chip.symbol}:{chip.issuer}",
            }
        return {
            "scam_risk": "unknown",
            "confidence": _CONFIDENCE["unknown"],
            "verdict_reason": "no_address_scoped_evidence",
        }

    if any(sev in _HIGH for sev in severities):
        return {
            "scam_risk": "high",
            "confidence": _CONFIDENCE["high"],
            "verdict_reason": "address_label_high",
        }
    if any(sev in _MEDIUM for sev in severities):
        return {
            "scam_risk": "medium",
            "confidence": _CONFIDENCE["medium"],
            "verdict_reason": "address_label_medium",
        }
    if all(sev in _LOW for sev in severities):
        return {
            "scam_risk": "low",
            "confidence": _CONFIDENCE["low"],
            "verdict_reason": "address_label_low_only",
        }
    return {
        "scam_risk": "unknown",
        "confidence": _CONFIDENCE["unknown"],
        "verdict_reason": "address_label_unclassified",
    }


__all__ = ["decide_verdict"]

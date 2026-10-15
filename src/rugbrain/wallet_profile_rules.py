"""Wallet-profile verdict rules — pure functions, no I/O.

Split out of :mod:`rugbrain.wallet_profile` so the classification rule is
unit-testable against real payload shapes without a live Qdrant, and so each
module stays inside the file-size cap.

The rule is stated, not asserted: a label is malicious if it carries a
malicious marker, benign if it carries a benign marker, and unclassified
otherwise. A hit in the scam corpus is a scam signal even without a marker
word. The worst level across all hits wins.
"""

from __future__ import annotations

from typing import Any

#: Collections holding per-address labels, in priority order. All three are in
#: the public allowlist (``knowledge_boundary_tables.PUBLIC_COLLECTIONS``), so
#: this is a public-surface read.
LABEL_COLLECTIONS: tuple[str, ...] = ("wallet_v2_8b", "wallet_profiles_8b", "known_scams_8b")

#: Label substrings that mark an address as malicious regardless of collection.
MALICIOUS_MARKERS: tuple[str, ...] = (
    "phish",
    "scam",
    "hack",
    "exploit",
    "malicious",
    "fraud",
    "rug",
    "sanction",
    "ofac",
    "stolen",
    "mixer",
    "tornado",
)

#: Label substrings that mark an address as attributed and benign.
BENIGN_MARKERS: tuple[str, ...] = (
    "exchange",
    "cex",
    "bridge",
    "protocol",
    "defi",
    "dex",
    "multisig",
    "treasury",
    "foundation",
    "deployer",
    "token",
    "nft",
    "dao",
    "staking",
    "lending",
)

#: risk_level -> (score, severity). The score is the midpoint of the band the
#: level names, so a consumer reading only the number still lands in the band.
LEVELS: dict[str, tuple[float, str]] = {
    "critical": (0.95, "critical"),
    "high": (0.75, "high"),
    "medium": (0.5, "medium"),
    "low": (0.15, "low"),
    "unknown": (0.0, "unknown"),
}

#: Severity order, most severe first. ``_worst`` walks this.
_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low", "unknown")


def label_text(payload: dict[str, Any]) -> str:
    """The searchable label text of one hit: label + name + entity_type."""
    meta = payload.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    parts = [
        str(meta.get("label") or ""),
        str(meta.get("name") or ""),
        str(meta.get("entity_type") or ""),
        str(payload.get("content") or ""),
    ]
    return " ".join(parts).lower()


def classify_hit(payload: dict[str, Any], collection: str) -> str:
    """Map one label hit to a risk level. Malicious beats benign beats unknown."""
    text = label_text(payload)
    if any(marker in text for marker in MALICIOUS_MARKERS):
        return "critical"
    if collection == "known_scams_8b":
        # A hit in the scam corpus is a scam signal even without a marker word.
        return "high"
    if any(marker in text for marker in BENIGN_MARKERS):
        return "low"
    return "medium"


def finding(payload: dict[str, Any], collection: str, level: str) -> dict[str, Any]:
    """One auditable finding: what was found, where, and why it scored."""
    meta = payload.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "collection": collection,
        "label": str(meta.get("label") or ""),
        "name": str(meta.get("name") or ""),
        "entity_type": str(meta.get("entity_type") or ""),
        "chain": str(meta.get("chain") or payload.get("chain") or ""),
        "source": str(meta.get("source") or payload.get("source") or ""),
        "risk_level": level,
        "content": str(payload.get("content") or "")[:300],
    }


def worst(levels: list[str]) -> str:
    """The most severe level present, by the declared band order."""
    for level in _ORDER:
        if level in levels:
            return level
    return "unknown"


def build_profile(address: str, hits: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Pure function: (address, [(collection, payload)]) -> the profile verdict.

    Split from the I/O so the verdict rule is unit-testable against real
    payload shapes without a live Qdrant.
    """
    findings = [finding(payload, coll, classify_hit(payload, coll)) for coll, payload in hits]
    if not findings:
        return {
            "address": address,
            "found": False,
            "risk_score": 0.0,
            "risk_level": "unknown",
            "severity": "unknown",
            "findings": [],
            "labels": [],
            "collections_searched": list(LABEL_COLLECTIONS),
            "note": (
                "no label found for this address in the wallet/scam corpora — "
                "this means UNSEEN, not clean"
            ),
        }

    level = worst([f["risk_level"] for f in findings])
    score, severity = LEVELS[level]
    labels = sorted({f["label"] for f in findings if f["label"]})
    return {
        "address": address,
        "found": True,
        "risk_score": score,
        "risk_level": level,
        "severity": severity,
        "findings": findings,
        "labels": labels,
        "collections_searched": list(LABEL_COLLECTIONS),
        "note": f"{len(findings)} label(s) matched; worst level {level}",
    }


__all__ = [
    "BENIGN_MARKERS",
    "LABEL_COLLECTIONS",
    "LEVELS",
    "MALICIOUS_MARKERS",
    "build_profile",
    "classify_hit",
    "finding",
    "label_text",
    "worst",
]

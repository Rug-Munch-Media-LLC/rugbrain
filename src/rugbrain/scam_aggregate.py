"""Aggregate raw threat-intel docs into structured, queryable scam patterns.

Groups docs by attack vector, then by technique (derived from indicators), and
emits one pattern per group with the strongest code snippet, affected
protocols, chain, severity, funds lost and reference incidents (source URLs).
Results are sorted severity-first, then by funds lost, so the most dangerous
patterns come first when they are displayed to users.

Pure: takes docs, returns docs. Nothing here touches Qdrant or Redis.
"""

from __future__ import annotations

from typing import Any

from rugbrain.scam_detect import detect_attack_vectors, detect_indicators, normalize_vector
from rugbrain.scam_fields import max_severity, parse_funds_lost
from rugbrain.scam_vocab import SEVERITY_RANK

__all__ = ["empty_group", "extract_scam_patterns_from_docs"]

_MAX_SNIPPET = 4000


def empty_group(vector: str, technique: str) -> dict[str, Any]:
    """A fresh pattern accumulator keyed by ``(vector, technique)``."""
    return {
        "pattern_id": f"{vector}:{technique}",
        "attack_type": vector,
        "technique": technique,
        "code_snippet": "",
        "affected_protocols": [],
        "chain": "",
        "severity": "info",
        "funds_lost": 0.0,
        "reference_incidents": [],
        "indicators": [],
        "doc_count": 0,
    }


def _merge_doc(group: dict[str, Any], doc: dict[str, Any], content: str) -> None:
    """Fold one threat-intel doc into an existing pattern group."""
    metadata = doc.get("metadata", {}) or {}
    protocol = str(metadata.get("protocol") or metadata.get("contract_name") or "").strip()
    if protocol and protocol not in group["affected_protocols"]:
        group["affected_protocols"].append(protocol)

    # Keep the longest code snippet (most representative PoC).
    if len(content) > len(group["code_snippet"]):
        group["code_snippet"] = content[:_MAX_SNIPPET]

    chain = str(metadata.get("chain") or "").strip()
    if chain and not group["chain"]:
        group["chain"] = chain

    group["severity"] = max_severity([group["severity"], doc.get("severity", "")])
    funds = parse_funds_lost(metadata.get("amount_lost_usd") or metadata.get("amount_lost"))
    group["funds_lost"] = max(group["funds_lost"], funds)

    source_url = metadata.get("source_url") or doc.get("source", "")
    if source_url and str(source_url) not in group["reference_incidents"]:
        group["reference_incidents"].append(str(source_url))

    for indicator in detect_indicators(content):
        if indicator not in group["indicators"]:
            group["indicators"].append(indicator)

    group["doc_count"] += 1


def extract_scam_patterns_from_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate raw threat-intel docs into structured scam patterns."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for doc in docs:
        metadata = doc.get("metadata", {}) or {}
        content = doc.get("content", "") or ""

        # Attack vector: prefer explicit metadata, else detect from code.
        vectors = detect_attack_vectors(content)
        vector = normalize_vector(metadata.get("attack_vector") or metadata.get("category"))
        if vector in ("unknown", "smart-contract-exploit") and vectors[0] != "unknown":
            vector = vectors[0]

        indicators = detect_indicators(content)
        technique = indicators[0] if indicators else vector

        group = grouped.setdefault((vector, technique), empty_group(vector, technique))
        _merge_doc(group, doc, content)

    patterns = list(grouped.values())
    patterns.sort(
        key=lambda p: (SEVERITY_RANK.get(p["severity"], 0), p["funds_lost"]),
        reverse=True,
    )
    return patterns

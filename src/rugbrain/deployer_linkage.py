"""Deployer -> token linkage: which contracts did this address deploy?

The gap this closes: ``scam_signatures`` holds labelled contracts but no
deployer index, so "has this address deployed anything malicious before" is
unanswerable. The Forta dataset (MIT, 139.6k rows) ships ``contract_creator``
alongside the ``malicious`` label — the join was simply never built.

This module is a PURE projection over rows the existing
``FortaMaliciousContractsSource`` already produces. It does not fetch, does not
embed and does not write, so it is unit-testable against real row shapes and
cannot silently degrade when a network call fails.

The risk rule is stated here rather than buried in a constant, because the
output is a claim about a real address:

  * ``critical`` — 3+ malicious contracts, or any malicious contract whose
    creator is also the creator of 5+ contracts total (a factory pattern).
  * ``high``     — 2+ malicious contracts.
  * ``medium``   — exactly 1 malicious contract.
  * ``low``      — contracts deployed, none labelled malicious.
  * ``unknown``  — no contracts attributed to this deployer.

A deployer with zero attributed contracts is ``unknown``, NOT ``low``: "we have
never seen this address deploy" is a different claim from "this address is
clean", and only the first is supportable from this data.
"""

from __future__ import annotations

from typing import Any

from rugbrain.ingest_sources.base import IngestDoc

__all__ = [
    "COLLECTION",
    "SOURCE",
    "deployer_risk",
    "link_deployer_tokens",
    "linkage_to_docs",
]

COLLECTION = "scam_signatures"
SOURCE = "forta_deployer_linkage"

#: A deployer at or above this many total contracts is treated as a factory.
FACTORY_THRESHOLD = 5
#: Malicious-contract count at which a deployer is critical regardless of size.
CRITICAL_MALICIOUS = 3


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def link_deployer_tokens(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group labelled contract rows by their creator address.

    *rows* are Forta-shaped: ``contract_address``, ``contract_creator``,
    ``malicious`` (bool), and optionally ``contract_tag`` / ``contract_name``.
    Rows with no creator or no contract address are skipped — an unattributed
    row is not evidence about any deployer.

    Returns ``{deployer_lowercase: entry}`` where each entry carries the token
    list, the malicious/benign split, and the derived risk verdict.
    """
    by_deployer: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        deployer = _text(row.get("contract_creator")).lower()
        address = _text(row.get("contract_address")).lower()
        if not deployer or not address:
            continue
        entry = by_deployer.setdefault(
            deployer,
            {"deployer": deployer, "tokens": [], "malicious_count": 0, "benign_count": 0},
        )
        if any(t["address"] == address for t in entry["tokens"]):
            continue  # the same contract twice is one contract
        malicious = bool(row.get("malicious"))
        entry["tokens"].append(
            {
                "address": address,
                "malicious": malicious,
                "tag": _text(row.get("contract_tag")),
                "name": _text(row.get("contract_name")),
            }
        )
        entry["malicious_count" if malicious else "benign_count"] += 1

    for entry in by_deployer.values():
        entry["total_count"] = len(entry["tokens"])
        entry["risk"] = deployer_risk(entry)
    return by_deployer


def deployer_risk(entry: dict[str, Any]) -> str:
    """Map a linkage entry to a verdict. See the module docstring for the rule."""
    total = int(entry.get("total_count") or 0)
    malicious = int(entry.get("malicious_count") or 0)
    if total == 0:
        return "unknown"
    if malicious >= CRITICAL_MALICIOUS:
        return "critical"
    if malicious >= 2:
        return "high"
    if malicious == 1:
        # A single malicious contract from a prolific deployer is a factory
        # pattern, not a one-off: escalate.
        return "critical" if total >= FACTORY_THRESHOLD else "medium"
    return "low"


def linkage_to_docs(linkage: dict[str, dict[str, Any]]) -> list[IngestDoc]:
    """One ``scam_signatures`` IngestDoc per deployer, keyed by deployer address.

    The doc is keyed on the DEPLOYER, not the token, so a lookup by deployer
    address returns the whole deployment history in one hit. ``doc_id`` is
    deterministic, so re-running the projection overwrites rather than
    duplicates.
    """
    docs: list[IngestDoc] = []
    for deployer, entry in sorted(linkage.items()):
        risk = str(entry.get("risk") or "unknown")
        malicious = int(entry.get("malicious_count") or 0)
        total = int(entry.get("total_count") or 0)
        lines = [
            f"Deployer {deployer} — {total} contract(s), {malicious} labelled malicious",
            f"Risk: {risk}",
        ]
        for token in entry.get("tokens", [])[:25]:
            verdict = "MALICIOUS" if token.get("malicious") else "benign"
            label = token.get("name") or token.get("tag") or ""
            lines.append(f"  {token.get('address')} [{verdict}] {label}".rstrip())

        docs.append(
            IngestDoc(
                content="\n".join(lines) + "\n",
                doc_id=f"{SOURCE}:{deployer}",
                metadata={
                    "collection": COLLECTION,
                    "entity_type": "deployer",
                    "entity": deployer,
                    "deployer": deployer,
                    "chain": "ethereum",
                    "severity": "critical"
                    if risk == "critical"
                    else "high"
                    if risk == "high"
                    else "low",
                    "category": "deployer-linkage",
                    "source": SOURCE,
                    "source_url": "https://huggingface.co/datasets/forta/malicious-smart-contract-dataset",
                    "knowledge_type": "deployer_token_linkage",
                    "labels": ["deployer-linkage", f"risk-{risk}"],
                    "deployer_risk": risk,
                    "deployer_malicious_count": malicious,
                    "deployer_total_count": total,
                    "deployer_token_addresses": [t["address"] for t in entry.get("tokens", [])],
                },
                doc_type="scam_signature",
                source=SOURCE,
                title=f"Deployer {deployer[:12]} — {malicious}/{total} malicious",
            )
        )
    return docs

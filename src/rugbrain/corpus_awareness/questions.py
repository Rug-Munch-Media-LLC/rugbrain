"""What question each collection answers — one line, and no invention.

An unmapped collection gets an explicit "unmapped" marker instead of a
plausible-sounding description: a wrong one-liner is worse than none, because
agents route by it.
"""

from __future__ import annotations

QUESTION_FOR: dict[str, str] = {
    "news_v2": "what happened in crypto news, and who reported it",
    "rag_documents": "which stored document covers this topic",
    "threat_intel_v2": "is this token / wallet / contract a known threat",
    "defi_exploits": "how was a DeFi protocol exploited, and by which bug class",
    "scam_patterns": "which rug or scam playbook matches this behaviour",
    "scam_intel_v2": "is this actor or campaign already known as a scam",
    "crypto_vulnerabilities": "which vulnerability class affects this code",
    "solidity_patterns": "what does working / malicious Solidity look like here",
    "solana_patterns": "what does working / malicious Solana code look like here",
    "contract_security": "which audit finding applies to this contract shape",
    "audit_findings": "has an auditor already found this, and how was it fixed",
    "coding_knowledge": "how do we implement this in a given VM / language",
    "known_scams": "which scam is already on record",
    "crime_reports": "which incident report documents this crime",
    "zeroday_intel": "is there a fresh zero-day or exploit-db signal",
    "internal_rmi_backend": "how does OUR rmi-backend implement this",
    "internal_rugbrain": "how does OUR rugbrain implement this",
    "codebase_knowledge": "how does our own code implement this",
}

UNMAPPED = "unmapped collection — name only, no verified question"


def question_for(name: str) -> str:
    """The one-line question a collection answers, or an explicit unmapped mark."""
    return QUESTION_FOR.get(name, UNMAPPED)

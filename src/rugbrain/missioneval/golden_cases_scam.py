"""
Mission golden set DATA — scam-detection rows.

Split out of ``golden_cases.py`` only to respect the 200-line rule. Same
contract; see ``goldens.py`` for the rationale and the grading model.

Kept as its own module because every row here is graded by an EXACT Qdrant
point id rather than by vocabulary. That is the strictest grading in the
whole set, and keeping the rows together makes it obvious when one of them
loses its id and silently falls back to a term match.
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

# fmt: off
SCAM_CASES: list[MissionCase] = [
    MissionCase(
        id="scam-01",
        job_class="scam_detection",
        query="ponzi scheme promising daily returns investment scam",
        collection="scam_patterns",
        relevant_doc_ids=["f42cb39c-0035-5078-8b79-97663b9dd98d"],
        match_terms=["ponzi"],
        partial_terms=["investment", "returns", "scamming"],
        provenance_note="CryptoScamDB ponzi point observed live 2026-09-28.",
    ),
    MissionCase(
        id="scam-02",
        job_class="scam_detection",
        query="fake exchange that scams deposits then claims withdrawal fees",
        collection="scam_patterns",
        relevant_doc_ids=["49af5773-a193-5a38-90d9-aad1e52563aa"],
        match_terms=["fake exchange", "category: scamming (exchange)"],
        partial_terms=["withdrawal", "deposit"],
        provenance_note="CryptoScamDB exchange-scam point observed live 2026-09-28.",
    ),
    MissionCase(
        id="scam-03",
        job_class="scam_detection",
        query="trust trading scam site lures users into fake profit",
        collection="scam_patterns",
        match_terms=["trust trading", "trust-trading"],
        partial_terms=["fake profit", "trading"],
        provenance_note="CryptoScamDB trust-trading points observed live 2026-09-28.",
    ),
    MissionCase(
        id="scam-04",
        job_class="scam_detection",
        query="phishing domain blacklisted that drains a connected wallet",
        collection="scam_intel_v2",
        relevant_doc_ids=["33ce83c4-683c-57e9-8660-08f302beafff"],
        match_terms=["phishing domain", "phishing"],
        partial_terms=["wallet drain", "drainer", "blacklist"],
        provenance_note="eth-phishing-detect point observed live 2026-09-28.",
    ),
    MissionCase(
        id="scam-05",
        job_class="scam_detection",
        query="fake airdrop claim site that steals seed phrase or drains wallet",
        collection="known_scams",
        relevant_doc_ids=["bbe92dca-0836-55e0-a68c-aae7af030ea8"],
        match_terms=["phishing", "airdrop", "claim"],
        partial_terms=["metamask", "scamsniffer"],
        provenance_note="ScamSniffer phishing-domain mapping point observed 2026-09-28.",
    ),
]
# fmt: on

__all__ = ["SCAM_CASES"]

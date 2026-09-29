"""
Mission golden set DATA — the NEGATIVE scam rows (Lane Y), split for the 200-line cap.

Every row in here is a question the crypto-crime corpus CANNOT answer, and the
right response is that it does not cover it. They are the honest half of the
scam gap: a golden set that only asks answerable questions teaches a retriever
to never say "I don't know".

**These are NOT registered in ``goldens.all_cases()`` and must not be until the
judge can score an absence.** ``judge.grade_hits`` has exactly three grades —
gold, partial, miss — and "the answer correctly is not in the corpus" is
indistinguishable from "retrieval failed". ``runner.run_mission_eval`` then
divides by ``len(valid)``, so five unconditional misses would cost
scam_detection roughly 40% of its gold_rate against a shrink-only 0.8 floor and
turn the gate red. Shipping them unregistered keeps the coverage statement
without the false regression. The lead's call: add a fourth grade scored on the
ABSENCE of a match, then register ``NEGATIVE_CASES`` here.
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

# Widths verified live 2026-09-29 on 127.0.0.1:6334 via lane_resolver:
#   scam_intel_v2 -> scam_intel_v2_8b 107,878 (4096) | known_scams -> known_scams_8b 26,720 (4096)
#   scam_patterns -> scam_patterns 7,102 (2560)      | wallet_profiles -> wallet_profiles_8b 9,657 (4096)
#   scam_pattern_signatures_8b: HTTP 404, DOES NOT EXIST (see xext-11).

# fmt: off
XEXT_SCAM_CASES: list[MissionCase] = [
    MissionCase(
        id="xext-09",
        job_class="scam_detection",
        query="is this crypto wallet address currently hosting a live drainer kit",
        collection="scam_intel_v2",
        relevant_doc_ids=[],
        match_terms=["drainer kit", "phishing kit"],
        partial_terms=["kit", "as a service", "source code"],
        provenance_note=(
            "NEGATIVE. The corpus carries 107,878 blacklist HITS — domains, labels, "
            "one phishing contract address per row — and ZERO drainer-as-a-service "
            "artefacts: no kit source, no builder URL, no campaign identifier. A "
            "'phishing kit' / 'drainer kit' text search returned 0 across "
            "scam_intel_v2_8b and known_scams_8b on 2026-09-29, which is why this "
            "row carries no id. The right answer is 'this corpus lists what was "
            "flagged, it does not carry kits'. Match terms are the ABSENT strings so "
            "a hit would be a real false-positive signal. UNREGISTERED — see module "
            "docstring: a correctly-absent answer is scored as a miss."
        ),
    ),
    MissionCase(
        id="xext-10",
        job_class="scam_detection",
        query="has this domain been taken down or is it still serving a phishing page",
        collection="known_scams",
        relevant_doc_ids=[],
        match_terms=["takedown", "taken down", "sinkholed", "status: offline"],
        partial_terms=["registrar", "active", "last seen"],
        provenance_note=(
            "NEGATIVE. Every known_scams_8b row (26,720) is a static listing stamped "
            "at ingest; nothing in the payload carries liveness, a sinkhole status, "
            "or a takedown timestamp — the schema has no field for it. This is the "
            "single most-requested question in scam triage and the corpus cannot "
            "answer it, which is exactly why a gold set should say so. Do not repoint "
            "this at news_v2: an article about a takedown is not evidence that THIS "
            "domain is down. UNREGISTERED — see module docstring."
        ),
    ),
    MissionCase(
        id="xext-11",
        job_class="scam_detection",
        query="aggregated recurring scam pattern across victims, the signature of a campaign",
        collection="scam_patterns",
        relevant_doc_ids=[],
        match_terms=["scam pattern signature", "aggregated pattern", "campaign signature"],
        partial_terms=["cluster", "recurring", "n=1", "single record"],
        provenance_note=(
            "NEGATIVE, and the most important row here. scam_patterns (7,102) is a "
            "PHANTOM for this purpose: every row is a single CryptoScamDB victim "
            "record, so nothing has EVER been aggregated out of it. The aggregated "
            "collection that was supposed to answer this, "
            "scam_pattern_signatures_8b, DOES NOT EXIST on 127.0.0.1:6334 — HTTP 404 "
            "for both the base and the _8b name, verified 2026-09-29, and absent "
            "from lane_resolver's table. So the honest answer is 'the corpus holds "
            "7,102 individual records and no aggregation has ever run'. The row is "
            "deliberately pointed at scam_patterns rather than at the missing "
            "collection: a collection that 404s would make the retriever raise, and "
            "an error is scored as unavailable, not as a clean miss. UNREGISTERED."
        ),
    ),
    MissionCase(
        id="xext-12",
        job_class="scam_detection",
        query="private key, seed phrase or keystore file of the scammer who stole the funds",
        collection="known_scams",
        relevant_doc_ids=[],
        match_terms=["private key", "seed phrase of the attacker", "keystore"],
        partial_terms=["attacker identity", "doxx", "law enforcement"],
        provenance_note=(
            "NEGATIVE and the row that MUST be refused rather than answered. A scam "
            "corpus can and should answer 'is this address flagged' (xext-01); it "
            "cannot and will not yield attacker secrets, and no report should read as "
            "a claim that we hold them. Marked negative with a match set of "
            "deliberately alarming strings so that any hit is a loud false positive "
            "rather than a quiet leak of a claim we cannot back. xext-13 is the "
            "in-corpus REFUSAL counterpart to this out-of-corpus one. UNREGISTERED."
        ),
    ),
    MissionCase(
        id="xext-13",
        job_class="scam_detection",
        query="current tax owed and next filing deadline for a DAO treasury address",
        collection="wallet_profiles",
        relevant_doc_ids=[],
        match_terms=["tax liability", "filing deadline", "tax owed"],
        partial_terms=["treasury", "jurisdiction", "dao"],
        provenance_note=(
            "NEGATIVE, and the in-corpus REFUSAL. The question is answerable only by "
            "a tax/jurisdiction dataset; wallet_profiles_8b (9,657) holds Etherscan "
            "entity labels and address attributions, not tax state, and the corpus "
            "has no tax provider. This is 'this corpus does not cover it' WITHOUT "
            "leaving the crypto-crime domain, which is the cheap way to mark an "
            "out-of-scope refusal: the retrieval lane still runs, so the refusal is "
            "demonstrated by an empty result rather than by refusing to search. "
            "UNREGISTERED — see module docstring."
        ),
    ),
]
# fmt: on

#: Ids are stable and nothing is deleted, so registering them later is a
#: one-line change to ``golden_cases_scam_ext`` and not a re-authoring.
NEGATIVE_CASES: list[MissionCase] = [
    c
    for c in XEXT_SCAM_CASES
    if c.id
    in {
        "xext-09",
        "xext-10",
        "xext-11",
        "xext-12",
        "xext-13",
    }
]

__all__ = ["NEGATIVE_CASES", "XEXT_SCAM_CASES"]

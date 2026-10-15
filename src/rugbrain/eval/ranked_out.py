"""Golden rows that are COVERED but still miss at top-5, with the diagnosis.

Coverage (``scripts/golden_coverage.py``) answers "does the anchor exist?".
This module answers the next question: "if it exists, why does the retriever
still miss it?" The two failure modes need different fixes and must never be
conflated:

  * ORPHAN (not covered) — the anchor is absent; re-anchor or drop the row.
  * RANKED-OUT (covered, misses) — the anchor exists but the query's vocabulary
    does not pull it into the top-k. Re-anchoring would be a LIE (the chunk is
    right); the honest fix is to reword the query to the corpus's own
    vocabulary, or accept the miss and record why.

Measured 2026-10-15 against live Qdrant with the direct-Qdrant retriever.
``rank`` is the position of the gold chunk in a 500-hit search of the resolved
collection; ``None`` means it did not appear in the first 500.
"""

from __future__ import annotations

from typing import Final

#: query -> {collection, rank, cause, detail}. Keyed by the golden row's query
#: so a reader can match it to ``golden_set.jsonl`` without an id lookup.
RANKED_OUT: Final[dict[str, dict[str, object]]] = {
    "zk circuit underconstrained proof vulnerability": {
        "collection": "security_patterns_8b",
        "rank": None,
        "cause": "query/chunk vocabulary mismatch",
        "detail": (
            "The gold chunk (3e112d3f-…, 'Underconstrained Circuit Enables Proof "
            "Forgery (Circom)') ranks #1 for its own title and for 'underconstrained "
            "circuit proof forgery circom', but the row's query uses 'zk circuit' + "
            "'vulnerability', which pull generic Solidity code snippets from the "
            "7,063-point twin. The chunk is correct; the query wording is not the "
            "corpus's vocabulary. Fix = reword the query, NOT re-anchor."
        ),
    },
    "MEV sandwich attack prevention": {
        "collection": "coding_knowledge_8b",
        "rank": 36,
        "cause": "ranking (weak lexical overlap)",
        "detail": (
            "Gold chunk ('can be submitted by anyone, they can be frontrun') exists "
            "and reaches rank 36 of 500. The query 'MEV sandwich attack prevention' "
            "shares little vocabulary with the chunk, which is about permit/frontrun "
            "mechanics. Covered, ranked out of top-5."
        ),
    },
    "oracle price manipulation flash loan lending": {
        "collection": "defi_exploits_8b",
        "rank": 374,
        "cause": "ranking (query too generic for a 16k-point collection)",
        "detail": (
            "Gold chunk ('turned a price or liquidity assumption into a system-wide "
            "loss') exists and reaches rank 374 of 500. The query is a broad concept "
            "that matches many exploit write-ups; the specific chunk is buried."
        ),
    },
    "honeypot token detected risk flags scanner": {
        "collection": "threat_intel_v2_8b",
        "rank": None,
        "cause": "query/chunk mismatch (mis-anchored row)",
        "detail": (
            "The gold chunk is a Telegram scam-alert post ('Skeleton Lounge: Sorcery "
            "(SOR)', id 0688c1c3-…) that merely contains the token 'SOR'. The query "
            "asks about a honeypot RISK-FLAG SCANNER concept; the anchor is an "
            "unrelated TG post. This row is mis-anchored — it should be re-anchored "
            "to a real honeypot-scanner chunk or dropped, NOT left as a permanent miss."
        ),
    },
    "mev sandwich attack front run back run": {
        "collection": "threat_intel_v2_8b",
        "rank": 270,
        "cause": "ranking (code-snippet anchor vs conceptual query)",
        "detail": (
            "Gold chunks ('function sandWich() internal', 'swap crvUSD to USDC') are "
            "raw Solidity from an exploit PoC and reach rank 270/252 of 500. The query "
            "is conceptual; the anchors are code. Covered, ranked out."
        ),
    },
}

__all__ = ["RANKED_OUT"]

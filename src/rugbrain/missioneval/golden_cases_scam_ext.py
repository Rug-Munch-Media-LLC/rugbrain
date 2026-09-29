"""Extended scam-detection and rug-history golden rows (Lane Y).

DATA ONLY. The provenance note — what this set adds, and that every collection
was resolved live through ``lane_resolver`` against Qdrant on 2026-09-29 — is
documentation, and it belongs in :mod:`rugbrain.missioneval.goldens`, which owns
the contract these rows are written against.

Split purely for the 200-line cap: 26 lines of prose on a 172-line table is a
documentation cost, not a reason to grow the module that explains the grading.
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

XEXT_SCAM_CASES: list[MissionCase] = [
    MissionCase(
        id="xext-01",
        job_class="scam_detection",
        query="is this specific address a known scammer or phishing wallet on chain",
        collection="known_scams",
        relevant_doc_ids=[
            "0007337e-4c96-59b9-a527-389054d91ec9",
            "0009a141-79e0-58be-8c05-84b09c9913a6",
        ],
        match_terms=["etherscan scam label", "category: malicious", "fake_phishing"],
        partial_terms=["flagged as scam", "malicious", "on ethereum"],
        provenance_note=(
            "Address-LEVEL lookup, the shape a user actually asks ('is THIS "
            "address bad'), which the 5 existing rows never do. Both ids observed "
            "live in known_scams_8b (26,720) 2026-09-29: 0xb2628597... and "
            "0xb467ca6b..., both Etherscan Fake_Phishing labels, Category: malicious. "
            "Two ids because a lookup question is satisfied by ANY of the labels, "
            "so the row is not hostage to one row surviving a re-ingest. Note the "
            "corpus answers the QUESTION FORM ('scam label: <addr> on <chain>') and "
            "not an arbitrary address: a clean 0x that is absent from 26,720 rows "
            "returns nothing, which is the honest miss-not-vind."
        ),
    ),
    MissionCase(
        id="xext-02",
        job_class="scam_detection",
        query="drainer contract that steals approved tokens from a victim's wallet",
        collection="scam_intel_v2",
        relevant_doc_ids=[
            "00063aa4-60a2-5a78-9f96-81e55953e480",
            "00164808-6157-56bb-8dd8-145fff28bfdf",
        ],
        match_terms=["drainer", "phishing contract", "labels: drainer"],
        partial_terms=["phishing", "blocksecteam", "0x"],
        provenance_note=(
            "Wallet-drainer coverage. scam_intel_v2 is where drainer ATTACK "
            "CONTRACTS live, as distinct from the phishing DOMAINS that scam-04 "
            "and xext-04 already use — the existing rows would have scored a drainer "
            "question by matching 'phishing', which is the wrong object: a drainer is "
            "an on-chain contract reached after the user signs, not a hostname. Both "
            "ids observed live in scam_intel_v2_8b (107,878) 2026-09-29; both carry "
            "the literal two-label string 'Labels: drainer, phishing'. Hardest of the "
            "drainer rows because the term must be the attack type, not the channel."
        ),
    ),
    MissionCase(
        id="xext-03",
        job_class="scam_detection",
        query="phishing site cloning a legitimate exchange or wallet to steal the seed phrase",
        collection="known_scams",
        relevant_doc_ids=["55997d60-e13c-5197-aa64-f287773ae6a1"],
        match_terms=["metamask-xpubs", "phishing for secrets", "api.telegram.org"],
        partial_terms=["myetherwallet", "category: phishing", "fake metamask"],
        provenance_note=(
            "Phishing CLONE detection, and the sharpest example in this module of a "
            "row that must NOT be credited by 'phishing' alone. scam-05 matches "
            "'phishing'+'airdrop'; this row's gold is the exfiltration half — the "
            "cloned MetaMask origin posting the mnemonic to a Telegram bot — which no "
            "existing row touches. Id observed live in scam_patterns (7,102) "
            "2026-09-29, not known_scams: the row is retrievable from either parent "
            "collection, and scam_patterns is where CryptoScamDB keeps a whole "
            "per-victim entry rather than a one-line blacklist hit, so a ranked "
            "retrieval surfaces more of the story. No seed phrase is quoted in the "
            "payload itself."
        ),
    ),
    MissionCase(
        id="xext-04",
        job_class="scam_detection",
        query="malicious URL reported by a phishing feed that should be blocked",
        collection="scam_intel_v2",
        relevant_doc_ids=[
            "4efc1160-0c86-5bdd-b0af-fff36b305543",
            "0002b520-e9d2-5fb8-ba8e-2d80a6889356",
        ],
        match_terms=["phishing domain", "blacklists phishing domain", "phishdestroy_destroylist"],
        partial_terms=["labels: phishing", "domains"],
        provenance_note=(
            "Feed-shaped phishing lookup: the question is 'a report came in, is it "
            "listed', not 'what is a phishing domain'. Both ids observed live in "
            "scam_intel_v2_8b 2026-09-29. The first is the impersonation shape — "
            "advertisers-client-support-impersonation-dispute.pages.dev, i.e. a fake "
            "ad-platform support case, a real and currently-active lure family that "
            "no existing row asks about. Deliberately easy-medium: this is 85% "
            "DOMAIN BLACKLISTS, so 'phishing domain' is genuinely native vocabulary "
            "and the row measures retrieval, not a hard distinction. That 85% is also "
            "why scam-04's framing is the weak one and this is the strong one."
        ),
    ),
    MissionCase(
        id="xext-05",
        job_class="rug_history",
        query="rug where the contract could mint unlimited supply and dumped on buyers",
        collection="rug_events",
        relevant_doc_ids=["0086f970-4245-517c-9845-13b1bca49aed"],
        match_terms=["unlimited mint & dump", "hidden owner", "holders at rug"],
        partial_terms=["mechanism:", "lp_drained", "infinite mint"],
        provenance_note=(
            "Rug MECHANICS by name, which the 5 existing rug rows never do — they "
            "ask about liquidity, ratios and holder counts, never about HOW. Id "
            "observed live in rug_events (1,877) 2026-09-29: BLK on BSC, Mechanism: "
            "unlimited mint & dump, hidden owner, Outcome: lp_drained, 103,798 "
            "holders at rug. Distinct from rug-05, which only asks for a documented "
            "row with a loss figure; this one grades on the MECHANISM string, which "
            "is the part an analyst actually needs. Do not expect a loss amount: the "
            "payload renders holders_at_rug but not a cash-out, so an id-level match "
            "is the only honest gold."
        ),
    ),
    MissionCase(
        id="xext-06",
        job_class="rug_history",
        query="token that claimed to be buy-only and could never be sold, holders trapped",
        collection="rug_events",
        relevant_doc_ids=["30d3e3c3-e5c2-5be7-bdf4-91a388ee4c56"],
        match_terms=["honeypot (buy-only, no sell)", "holders at rug", "lp_drained"],
        partial_terms=["buy-only", "mechanism:", "only in, not out"],
        provenance_note=(
            "A REAL historical honeypot with a real id — the companion to rug-01, "
            "which is a vocabulary-only row with NO id because no single rug_events "
            "row was then the canonical honeypot case. That gap is closed here: id "
            "observed live in rug_events 2026-09-29 is mSHIB on BSC, Mechanism: "
            "honeypot (buy-only, no sell) + unlimited mint & dump, only 32 holders at "
            "rug. The goPlus early-warning line renders the Chinese literal "
            "貔貅合约（只进不出）, so 'honeypot' alone must be a match_term and not a "  # noqa: RUF001
            "partial — the source is bilingual and an English-only match set would "
            "silently miss half the evidence. HARDEST row in the module: buy, "
            "honeypot, rug and trapped-holders all pull against each other."
        ),
    ),
    MissionCase(
        id="xext-07",
        job_class="scam_detection",
        query="impostor site posing as wallet support to harvest a seed phrase",
        collection="scam_intel_v2",
        relevant_doc_ids=["4efc1160-0c86-5bdd-b0af-fff36b305543"],
        match_terms=["impersonation", "phishing domain"],
        partial_terms=["support", "dispute", "labels: phishing"],
        provenance_note=(
            "Social-engineering lure, the layer under the phishing: the corpus "
            "records the HOST, not the story, so a top-5 hit is credited on the "
            "impersonation host string it actually contains rather than on any "
            "narrative it implies. Same id as xext-04, deliberately: this is the "
            "one id in the module that is right for two different questions, and "
            "that is a feature — it proves the grader is keying on the ask. Included "
            "to hold a mid slot that is neither the easiest blacklist lookup nor the "
            "hardest drainer-contract lookup."
        ),
    ),
    MissionCase(
        id="xext-08",
        job_class="scam_detection",
        query="what recovery service claims it can get back funds taken by a scammer",
        collection="scam_patterns",
        relevant_doc_ids=["84cfecf1-0efc-5f96-952a-4bebc9d3b65b"],
        match_terms=["scamming (recovery)", "fake fund recovery"],
        partial_terms=["fundsretrieve", "recovery", "fake"],
        provenance_note=(
            "The SECOND-HOP scam: the victim is already defrauded, and the follow-up "
            "is a 'recovery service' charging more. CryptoScamDB carries it as its "
            "own category, so it is the one scam family the existing ponzi / "
            "exchange / trust-trading rows structurally cannot reach. Id observed "
            "live in scam_patterns (7,102) 2026-09-29: fundsretrieve.com, Category: "
            "Scamming (Recovery), Description: Fake fund recovery service, no "
            "associated address. Graded on the category string because that is the "
            "only thing the corpus knows."
        ),
    ),
]
# fmt: on

__all__ = ["XEXT_SCAM_CASES"]

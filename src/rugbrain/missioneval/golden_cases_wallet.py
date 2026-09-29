"""
Mission golden set DATA — sanctions-screening and research rows.

Split out of the address-risk rows only to respect the 200-line rule. Same
contract; see ``goldens.py`` for the rationale and the grading model.

COLLECTION NAMING — LOAD-BEARING, applies to this whole package
    ``missioneval.collection_retriever`` embeds on the 2560 corpus lane
    (``CORPUS_DIM``) and passes ``MissionCase.collection`` to Qdrant
    VERBATIM. It does not call ``qdrant_store.lane_resolver``. So the 4096
    ``_8b`` twins — the lane the rest of the repo writes to — cannot be
    searched by this retriever: ``regulation_enforcement_8b`` returns
    ``400 expected dim: 4096, got 2560``, which surfaces as a per-case ERROR,
    not a miss. Every row below therefore names the 2560 BASE collection.
    Both lanes were verified to hold the SAME point ids, so these ids are
    correct for either name; only the base is reachable today.

    A row naming a 4096 twin would not quietly under-score — it would add an
    error to the report, which is a worse failure mode than a miss.

NEGATIVE ROWS ARE A DELIBERATE FEATURE, NOT A GAP
    ``sanction-03`` and ``research-03`` are scored on the correct answer being
    ABSENT from the corpus. They keep a future ingestion honest: if real
    OpenSanctions attributions or real paper abstracts ever reach the vector
    path, these rows must start scoring, and the reviewer should raise their
    weight. Until then they measure the boundary of what this system can
    honestly answer, which is the thing a crime-intake assistant must not get
    wrong.

CORRECTION TO A PRIOR ASSUMPTION
    Sanctions data is NOT missing. ``regulation_enforcement`` holds 5,081
    points, 5,013 of them ``category="sanctions"`` from OpenSanctions and
    1,058 tagged ``us_ofac_sdn``. The class is a real capability, so it is
    graded like one — except ``sanction-03``, where the corpus genuinely stops.
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

# fmt: off
SANCTION_CASES: list[MissionCase] = [
    MissionCase(
        id="sanction-01",
        job_class="sanctions_screening",
        query="is this wallet address on the OFAC SDN sanctioned entities list",
        collection="regulation_enforcement",
        relevant_doc_ids=[
            "005b565a-3c28-51f6-807d-e27bbe6ea727",
            "2cdd7700-9a81-5f02-b9ef-cd9da77fd407",
        ],
        match_terms=["us_ofac_sdn", "sanctioned entity"],
        partial_terms=["opensanctions", "sanction"],
        provenance_note=(
            "regulation_enforcement rows observed live 2026-09-28. The first id "
            "is an EVM address with source dataset us_ofac_sdn, the second is one "
            "the live retriever actually returned for this query. "
            "difficulty=medium."
        ),
    ),
    MissionCase(
        id="sanction-02",
        job_class="sanctions_screening",
        query="is this address on a national watchlist rather than the sanctions list",
        collection="regulation_enforcement",
        relevant_doc_ids=["0af3f306-3d2e-5745-88d5-a88512d3ba7b"],
        match_terms=["fr_tresor_gels_avoir", "watchlist"],
        partial_terms=["sanction", "opensanctions"],
        provenance_note=(
            "Only 6 rows in 5,081 carry label=watchlist; the id observed is a "
            "fr_tresor_gels_avoir entry. The watchlist/sanctions distinction is "
            "real compliance semantics, so this is deliberately hard: a system "
            "that conflates the two fails a KYC review. difficulty=hard."
        ),
    ),
    MissionCase(
        id="sanction-03",
        job_class="sanctions_screening",
        query="which named individual or entity does this sanctioned wallet belong to",
        collection="regulation_enforcement",
        relevant_doc_ids=[],
        match_terms=["alias", "sanctioned entity name", "person"],
        partial_terms=["opensanctions", "sanctioned entity"],
        provenance_note=(
            "NEGATIVE. The OpenSanctions rows carry the WALLET STRING plus a "
            "source dataset, and nothing else — scrolling all 5,081 rows showed "
            "no alias or person-name field on the sanction_entity records. So the "
            "corpus answers 'is it listed' but NOT 'who is it', which is exactly "
            "the attribution question a sanctions analyst asks first. A system "
            "that answers this is fabricating. difficulty=hard — REVIEWER note 3."
        ),
    ),
]

RESEARCH_CASES: list[MissionCase] = [
    MissionCase(
        id="research-01",
        job_class="research_papers",
        query="security research writeup showing how a DAO is hacked",
        collection="research_v2",
        relevant_doc_ids=["0bafc7ea-ff30-5bf0-92f8-b51128343c8c"],
        match_terms=["how to hack a dao", "dao"],
        partial_terms=["governance", "social layer"],
        provenance_note=(
            "research_v2 neodyme_blog_rss point, observed live. research_v2 holds "
            "158 points and is the only research collection; these are vendor and "
            "research-shop BLOG POSTS. difficulty=medium."
        ),
    ),
    MissionCase(
        id="research-02",
        job_class="research_papers",
        query="technical writeup on how consensus reaches finality in a proof of stake chain",
        collection="research_v2",
        relevant_doc_ids=["8db0fc2c-8892-5299-975a-be9eb633b7c8"],
        match_terms=["solana consensus", "finality"],
        partial_terms=["consensus", "fork"],
        provenance_note=(
            "'Solana Consensus - From Forks to Finality', neodyme_blog_rss, "
            "returned by the live retriever for this query. difficulty=medium."
        ),
    ),
    MissionCase(
        id="research-03",
        job_class="research_papers",
        query="peer reviewed academic paper with full text and methodology on AMM security",
        collection="research_v2",
        relevant_doc_ids=[],
        match_terms=["arxiv", "peer reviewed", "methodology", "abstract"],
        partial_terms=["security", "amm"],
        provenance_note=(
            "NEGATIVE. research_v2 is RSS ingestion — every sampled record is a "
            "title plus a one-to-two sentence lede and a Source URL (zellic, "
            "immunefi, neodyme, trailofbits blogs), not a paper. There is no "
            "full text and no methodology in the corpus, so a question demanding "
            "one must be refused rather than answered from a headline. "
            "difficulty=hard — REVIEWER note 4."
        ),
    ),
]
# fmt: on

__all__ = ["RESEARCH_CASES", "SANCTION_CASES"]

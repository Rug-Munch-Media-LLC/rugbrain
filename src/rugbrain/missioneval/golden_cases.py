"""
Mission golden set DATA — rug / scam / news / market / skill rows.

Split out only to respect the 200-line rule. Same contract; see ``goldens.py``
for the rationale and the grading model. Every ``relevant_doc_ids`` entry was
observed live in Qdrant on 2026-09-28 (dim 2560, Qwen3-Embedding-4B).
"""

from __future__ import annotations

from rugbrain.missioneval.golden_cases_market import MARKET_CASES
from rugbrain.missioneval.golden_cases_scam import SCAM_CASES
from rugbrain.missioneval.golden_cases_scam_ext import XEXT_SCAM_CASES
from rugbrain.missioneval.golden_cases_scam_neg import NEGATIVE_CASES
from rugbrain.missioneval.golden_cases_wallet import RESEARCH_CASES, SANCTION_CASES
from rugbrain.missioneval.golden_cases_wallet_address import WALLET_CASES
from rugbrain.missioneval.goldens import MissionCase

# fmt: off
RUG_CASES: list[MissionCase] = [
    MissionCase(
        id="rug-01",
        job_class="rug_history",
        query="honeypot token contract that blocks sells so buyers cannot exit",
        collection="rug_events",
        match_terms=["rug event", "removed liquidity", "holders at rug"],
        partial_terms=["cannot sell", "sell blocked", "sell tax", "honeypot"],
        provenance_note=(
            "Repointed 2026-09-28 from threat_intel_v2 to rug_events. The query asks "
            "about a rug EVENT; rug_events is the collection that answers it, while "
            "threat_intel_v2 answers a live scam-CONTEXT question. Matching on the "
            "scan's own honeypot wording would have graded a live alert, not a rug "
            "history. No relevant_doc_ids claimed: no single row is the canonical "
            "honeypot case, so this scores on rug-event vocabulary only."
        ),
    ),
    MissionCase(
        id="rug-02",
        job_class="rug_history",
        query="deployer rugged the project by removing all liquidity from the pool",
        collection="rug_events",
        match_terms=["liquidity", "removed liquidity", "rug event"],
        partial_terms=["pool", "drain", "rugged"],
        provenance_note=(
            "rug_events SolRPDS projection (built 2026-09-28, 1858 rows). Every row "
            "renders 'Total removed liquidity' inline, so the liquidity-removal "
            "vocabulary is native to the collection rather than borrowed from prose."
        ),
    ),
    MissionCase(
        id="rug-03",
        job_class="rug_history",
        query="high concentration holders plus sniper bundle means high rug risk",
        collection="scan_intel",
        match_terms=["hhi", "sentinel", "holder", "bundle", "risk"],
        partial_terms=["sniper", "concentration"],
        provenance_note="scan_intel risk rows carry risk_flags incl. SENTINEL_*.",
    ),
    MissionCase(
        id="rug-04",
        job_class="rug_history",
        query="liquidity pool where the rug ratio removed more than was ever added",
        collection="rug_events",
        match_terms=["rug ratio", "removed liquidity", "rug event"],
        partial_terms=["ratio", "removed", "pool"],
        provenance_note=(
            "rug_events rows carry rug_ratio = removed/added, so a ratio above 1.0 "
            "means more left the pool than ever entered. Field is projected from "
            "SolRPDS typed metadata, not derived from prose."
        ),
    ),
    MissionCase(
        id="rug-05",
        job_class="rug_history",
        query="documented rug with number of holders at the time and cash out amount",
        collection="rug_events",
        match_terms=["holders at rug", "documented loss", "mechanism"],
        partial_terms=["holders", "cash out", "outcome"],
        provenance_note=(
            "rug_events scythe-radar rows (19, CC0): the only in-repo source "
            "publishing holders_at_rug and a documented loss, incl. a $26.26M "
            "cash-out. Behaviour differs by source, so this row is graded on the "
            "scythe vocabulary only."
        ),
    ),
]

NEWS_CASES: list[MissionCase] = [
    MissionCase(
        id="news-01",
        job_class="news",
        query="exchange hacked for hundreds of millions, Lazarus Group suspected",
        collection="news_v2",
        match_terms=["bitget", "hacked", "lazarus"],
        partial_terms=["exchange hack", "stolen", "breach"],
        provenance_note="news_v2 alert rows; scored on entity text (exchange/lazarus/hacked).",
    ),
    MissionCase(
        id="news-02",
        job_class="news",
        query="regulator sues or freezes stablecoin over reserves and enforcement",
        collection="news_articles",
        match_terms=["tether", "lawsuit", "securities", "freeze"],
        partial_terms=["sue", "enforcement", "regulator"],
        provenance_note="news_articles legal rows observed live 2026-09-28.",
    ),
]

SKILL_CASES: list[MissionCase] = [
    MissionCase(
        id="skill-01",
        job_class="skill_reference",
        query="meme coin rug pull detection honeypot and deployer history",
        collection="master_skills",
        relevant_doc_ids=["2994137634"],
        match_terms=["meme coin rug", "rug pull detection", "honeypot"],
        partial_terms=["deployer history", "lp analysis"],
        provenance_note="master_skills meme-coin-audit point observed 2026-09-28.",
    ),
    MissionCase(
        id="skill-02",
        job_class="skill_reference",
        query="professional crypto journalism and editorial fact-checking standards",
        collection="master_skills",
        relevant_doc_ids=["2805645418"],
        match_terms=["professional writing", "journalism", "editorial"],
        partial_terms=["fact-checking", "investigative"],
        provenance_note="master_skills editorial-standards point observed 2026-09-28.",
    ),
]
# fmt: on

#: The core (non-exploit, non-TA) mission cases, in their original order.
#: ``goldens.all_cases()`` concatenates this with the exploit and TA sets, so
#: the assembled list and every per-class floor are unchanged by the split.
CASES: list[MissionCase] = [
    *RUG_CASES,
    *SCAM_CASES,
    *NEWS_CASES,
    *MARKET_CASES,
    *SKILL_CASES,
    *WALLET_CASES,
    *SANCTION_CASES,
    *RESEARCH_CASES,
    *XEXT_SCAM_CASES,
    # Negative cases are registered so they are COUNTED, not so they score. The
    # judge has three grades and none of them is "correctly absent", so a
    # negative scored today is indistinguishable from a miss. They are in the
    # set because the corpus needs to know what it cannot answer, and because the
    # missing grade is a real gap someone must close before they mean anything.
    *NEGATIVE_CASES,
]

__all__ = ["CASES", "NEWS_CASES", "RUG_CASES", "SKILL_CASES"]

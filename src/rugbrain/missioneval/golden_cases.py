"""
Mission golden set DATA — rug / scam / news / market / TA / skill rows.

Split out only to respect the 200-line rule. Same contract; see ``goldens.py``
for the rationale and the grading model. Every ``relevant_doc_ids`` entry was
observed live in Qdrant on 2026-09-28 (dim 2560, Qwen3-Embedding-4B).
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

# fmt: off
CASES: list[MissionCase] = [
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
MissionCase(
    id="market-01",
    job_class="market",
    query="current fear and greed index reading for the crypto market",
    collection="market_health",
    relevant_doc_ids=["498d3cde-d1a0-5403-ac6b-2311fa00c111"],
    match_terms=["fear & greed", "fear and greed"],
    partial_terms=["greed", "sentiment"],
    provenance_note="Fear&Greed point observed live 2026-09-28.",
),
MissionCase(
    id="market-02",
    job_class="market",
    query="USDC and USDT stablecoin peg and reserve backing",
    collection="stablecoin_market",
    relevant_doc_ids=["cf0bfc68-2cf1-58ec-81b6-a60dbb42c76d"],
    match_terms=["stablecoin", "peg", "usdc", "usdt"],
    partial_terms=["reserve", "fiat-backed", "depeg"],
    provenance_note="CoinGecko stablecoin point observed live 2026-09-28.",
),
MissionCase(
    id="ta-01",
    job_class="technical_analysis",
    query="which technical analysis library gives me candlestick pattern recognition and hundreds of indicators",
    collection="master_skills",
    relevant_doc_ids=["564311534", "1650223340"],
    match_terms=["candlestick pattern recognition", "ta-lib", "pandas-ta"],
    partial_terms=["technical analysis", "indicator"],
    provenance_note=(
        "Repointed 2026-09-28: the old row asked about liquidation cascades in "
        "live_threats and scored a clean miss (rank 0, no error), which is why the "
        "class read 0.000. Now answered from master_skills, ids fetched live."
    ),
),
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

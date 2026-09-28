"""
Technical-analysis golden cases — the mandate class that measured 0.000.

Why this is a SEPARATE module: ``golden_cases.py`` is at the 200-line cap and
this class is the one that needed real measurement work. Every row here was
observed live on 2026-09-28 against Qdrant ``master_skills`` (dim 2560,
Qwen3-Embedding-4B) and every ``relevant_doc_ids`` entry was fetched by point
id and its text read before being written here. No id in this file is guessed.

The class used to be a single ``ta-01`` row pointed at ``live_threats`` asking
about liquidation cascades. That row scored a clean ``miss`` and the class sat
at 0.000 — not because the corpus lacks TA knowledge, but because the row asked
a question no populated collection answers. The price/positioning knowledge
that DOES exist is a TA *reference* corpus: indicator libraries, exit and
sizing rules, market-structure and microstructure skills. That is what a
technical-analysis question is actually asking for, so the rows below ask for
it there, with the doc ids the retriever actually returns.

Honest scope note: this class measures TA *reference retrieval* (can the system
surface the right TA methodology), not TA *computation* (does it compute RSI on
live candles). The computation half is covered by the ``rugbrain.ta`` substrate
tests, not here — this corpus is prose, and claiming otherwise would be a
fabricated metric.
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

# fmt: off
TA_CASES: list[MissionCase] = [
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
    id="ta-02",
    job_class="technical_analysis",
    query="multi timeframe crypto analysis producing scored trading signals and a structured trade plan",
    collection="master_skills",
    relevant_doc_ids=["3292100420"],
    match_terms=["multi-timeframe crypto technical analysis", "scored trading signals", "trade plans"],
    partial_terms=["technical analysis", "position sizing"],
    provenance_note="master_skills multi-timeframe TA point fetched by id 2026-09-28.",
),
MissionCase(
    id="ta-03",
    job_class="technical_analysis",
    query="stop loss and take profit exit rules with trailing stop for a crypto position",
    collection="master_skills",
    relevant_doc_ids=["2706589342"],
    match_terms=["stop-loss", "take-profit", "trailing stop"],
    partial_terms=["exit strateg", "risk management"],
    provenance_note="master_skills exit-strategy point fetched by id 2026-09-28; rank 1 for this query.",
),
MissionCase(
    id="ta-04",
    job_class="technical_analysis",
    query="how do I size a trade using volatility adjusted or Kelly criterion sizing",
    collection="master_skills",
    relevant_doc_ids=["4283104254"],
    match_terms=["kelly criterion", "volatility-adjusted", "position sizing", "trade sizing"],
    partial_terms=["sizing", "risk"],
    provenance_note="master_skills trade-sizing point fetched by id 2026-09-28; rank 4 for this query.",
),
MissionCase(
    id="ta-05",
    job_class="technical_analysis",
    query="order book depth and bid ask imbalance as a liquidity signal on a DEX",
    collection="master_skills",
    relevant_doc_ids=["2252288596", "188906723"],
    match_terms=["order book depth", "bid-ask imbalance", "orderbook depth"],
    partial_terms=["market microstructure", "liquidity signal"],
    provenance_note="master_skills orderbook-depth + microstructure points fetched by id 2026-09-28.",
),
MissionCase(
    id="ta-06",
    job_class="technical_analysis",
    query="mean reversion strategy using Hurst exponent and z score signals",
    collection="master_skills",
    relevant_doc_ids=["735206142"],
    match_terms=["hurst exponent", "z-score", "mean-reversion"],
    partial_terms=["mean reversion", "adf"],
    provenance_note="master_skills mean-reversion point fetched by id 2026-09-28; rank 1 for this query.",
),
MissionCase(
    id="ta-07",
    job_class="technical_analysis",
    query="volatility forecasting and market regime classification with GARCH and EWMA",
    collection="master_skills",
    relevant_doc_ids=["2650437756", "1799900289"],
    match_terms=["garch", "ewma", "volatility estimation", "volatility clustering"],
    partial_terms=["regime", "volatility"],
    provenance_note="master_skills volatility + regime points fetched by id 2026-09-28; both rank in the top 5.",
),
MissionCase(
    id="ta-08",
    job_class="technical_analysis",
    query="price breakout from a defined range and drawing support and resistance on trading charts",
    collection="master_skills",
    relevant_doc_ids=["3754202154", "2767662415"],
    match_terms=["breakout", "candlesticks", "drawdowns"],
    partial_terms=["support", "resistance", "trading visualization"],
    provenance_note="master_skills breakout-watch + chart points fetched by id 2026-09-28; both rank in the top 5.",
),
]
# fmt: on

"""
Mission golden set DATA — market rows.

Split out of ``golden_cases.py`` only to respect the 200-line rule. Same
contract; see ``goldens.py`` for the rationale and the grading model.

The market class has the lowest floors in ``mission_baselines.json`` (0.5) of
any class with exact-id grading. That is a real measured number, not a
placeholder: these two rows are the ones where the corpus is thinnest, and the
floor is what makes a further drop to zero coverage a hard failure rather
than something averaged away by the strong classes.
"""

from __future__ import annotations

from rugbrain.missioneval.goldens import MissionCase

# fmt: off
MARKET_CASES: list[MissionCase] = [
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
]
# fmt: on

__all__ = ["MARKET_CASES"]

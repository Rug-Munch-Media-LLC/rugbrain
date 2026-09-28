"""Declared corpora, group: ops.

News and market feeds.

Every row is transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1 (96 collections,
6,314,262 points, dims {1024, 2560, 4096}). LANGUAGE and DOMAIN are what this file adds:
the audit measured the collections but never said which axis a repo belonged to, which is
how the Solidity-repos-are-the-code-corpus belief survived. See ``corpus/entries.py``.

See ``corpus/schema.py`` for what each field means and ``corpus/gate.py`` for the refusal.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import DAILY, HOURLY, WEEKLY, D, L, _d
from rugbrain.corpus.schema import (
    LICENCE_PERMISSIVE,
    CorpusDeclaration,
)
from rugbrain.qdrant_store.lane_table import PRESERVED_LANE_DIM

_NEWS_MARKET: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "news-v2",
        "news_v2",
        cid="news_v2",
        language=L.MARKDOWN,
        domain=D.NEWS,
        url="internal://pipeline/news-v2",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="162,476 pts. Live (0d). Base 2560, twin is the 4096 serving lane.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "news-articles-legacy",
        "news_articles",
        cid="news_articles",
        language=L.MARKDOWN,
        domain=D.NEWS,
        url="internal://pipeline/news-articles",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="2,364 pts. Superseded by news_v2; staleness is cosmetic. Base 2560.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "newsletters",
        "newsletters",
        cid="newsletters",
        language=L.MARKDOWN,
        domain=D.NEWS,
        url="internal://pipeline/newsletters",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="17 pts. Named vector; already canonical, no twin.",
        observed=4096,
        retired=()
    ),
    _d(
        "podcast-episodes",
        "podcast_episodes",
        cid="podcast_episodes",
        language=L.DOCS,
        domain=D.NEWS,
        url="internal://pipeline/podcasts",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="26 pts. Named vector; already canonical, no twin.",
        observed=4096,
        retired=()
    ),
    _d(
        "market-data",
        "market_data",
        cid="market_data",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/market-data",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="467 pts. Already canonical at 4096, no twin.",
        observed=4096,
    ),
    _d(
        "market-health",
        "market_health",
        cid="market_health",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/market-health",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="627 pts, 17d stale (audit §5.1).",
        observed=2560,
        retired=(),
    ),
    _d(
        "stablecoin-market",
        "stablecoin_market",
        cid="stablecoin_market",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/stablecoin-market",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="336 pts.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "exchange-flows",
        "exchange_flows",
        cid="exchange_flows",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/exchange-flows",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="Pinned 1024 (retired lane). 0 points.",
        observed=1024,
        retired=()
    ),
    _d(
        "hyperliquid-flows",
        "hyperliquid",
        cid="hyperliquid",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/hyperliquid",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="Pinned 1024. 0 points.",
        observed=1024,
        retired=()
    ),
    _d(
        "liquidation-events",
        "liquidation_events",
        cid="liquidation_events",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/liquidation-heatmap",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="Pinned 1024. 0 points.",
        observed=1024,
        retired=()
    ),
    _d(
        "token-unlocks",
        "token_unlocks",
        cid="token_unlocks",
        language=L.DATA,
        domain=D.MARKET,
        url="internal://pipeline/token-unlocks",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="Pinned 1024. 0 points.",
        observed=1024,
        retired=()
    ),
    _d(
        "social-intel",
        "social_intel",
        cid="social_intel",
        language=L.DATA,
        domain=D.NEWS,
        url="internal://pipeline/social-intel",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="20,984 pts at 2560. Twin exists but is NOT owned by this repo.",
        observed=2560,
        retired=(),
    ),
)

__all__ = [
    "_NEWS_MARKET",
]

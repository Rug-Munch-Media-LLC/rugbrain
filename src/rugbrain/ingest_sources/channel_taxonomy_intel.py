"""Informational-lane keyword needles: research, data, market, news.

The second half of the ordered taxonomy table; the crime half lives in
:mod:`rugbrain.ingest_sources.channel_taxonomy_crime`. These run after the risk
lanes so a "Scam Report" channel stays fraud rather than becoming research.

Data only. Matching is word-bounded via
:mod:`rugbrain.ingest_sources.channel_naming`; first match wins.
"""

RESEARCH_NEEDLES: tuple[str, ...] = (
    "research",
    "report",
    "reports",
    "paper",
    "papers",
    "whitepaper",
    "education",
    "academic",
    "academia",
    "study",
    "deep dive",
    "primer",
    "academy",
    "course",
    "thesis",
    "book club",
    "annotated",
)

DATA_NEEDLES: tuple[str, ...] = (
    "data",
    "analytics",
    "onchain",
    "on chain",
    "whale",
    "whales",
    "tvl",
    "metrics",
    "explorer",
    "explorers",
    "tracker",
    "trackers",
    "dashboard",
    "charts",
    "heatmap",
    "flows",
    "nansen",
    "glassnode",
    "arkham",
    "dune",
    "defillama",
    "defi",
    "revenue",
    "liq",
    "liquidations",
)

MARKET_NEEDLES: tuple[str, ...] = (
    "market",
    "markets",
    "trading",
    "trade",
    "signals",
    "signal",
    "futures",
    "perps",
    "perpetual",
    "perpetuals",
    "price",
    "prices",
    "btc",
    "bitcoin",
    "ethereum",
    "solana",
    "altcoin",
    "altcoins",
    "marketplace",
)

NEWS_NEEDLES: tuple[str, ...] = (
    "news",
    "newsroom",
    "press",
    "journal",
    "journalism",
    "times",
    "wire",
    "daily",
    "broadcast",
    "broadcasts",
    "herald",
    "desk",
    "post",
    "posts",
    "updates",
    "bulletin",
    "feeds",
    "digest",
    "headlines",
)

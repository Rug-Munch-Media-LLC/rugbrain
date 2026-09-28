"""Scoring tables for social ingest: category, importance and tickers.

Shared by the t.me/s web path and the Telegram userbot, which must agree or the
same story scores differently depending on which account read it. Extracted from
`telegram_web` to keep that module inside the 200-line limit and to give the
policy one home.
"""

from __future__ import annotations

import re

CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "scam_caller",
        ("scam", "fraud", "call out", "callout", "expose", "rug alert", "honeypot", "rugpull"),
    ),
    (
        "shiller",
        ("shill", "alpha caller", "calls", "100x", "gem", "moonshot", "degen", "ape", "pump"),
    ),
    (
        "security",
        ("security", "exploit", "hack", "certik", "blocksec", "cisa", "phishing", "audit"),
    ),
    (
        "news",
        (
            "news",
            "journal",
            "times",
            "post",
            "wire",
            "daily",
            "broadcast",
            "herald",
            "media",
            "feed",
            "digest",
        ),
    ),
    (
        "data",
        (
            "data",
            "analytics",
            "onchain",
            "on-chain",
            "whale",
            "defi",
            "tvl",
            "metrics",
            "scanner",
            "tracker",
        ),
    ),
    (
        "market",
        (
            "market",
            "trading",
            "signals",
            "futures",
            "perps",
            "btc",
            "eth",
            "solana",
            "crypto",
            "coin",
            "token",
        ),
    ),
]


CATEGORY_WEIGHT = {
    "scam_caller": 1.4,
    "security": 1.35,
    "shiller": 1.1,
    "news": 1.0,
    "data": 0.95,
    "market": 0.8,
    "general": 0.7,
}
IMPORTANCE_KEYWORDS = {
    "hack": 3.0,
    "exploit": 3.0,
    "drained": 3.0,
    "stolen": 2.5,
    "rug": 2.5,
    "rugpull": 3.0,
    "honeypot": 2.5,
    "scam": 2.0,
    "liquidity": 1.5,
    "audit": 1.0,
    "warning": 1.5,
    "alert": 1.5,
}
TICKER_RE = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,15}\b")
CA_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")

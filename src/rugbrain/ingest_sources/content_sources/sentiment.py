"""Per-item sentiment and ticker extraction, with a stated confidence.

Deterministic lexicon scoring, deliberately not an LLM call: the pipeline runs
thousands of items per cycle, and a sentiment that changes between identical
inputs cannot be cited or tested. Confidence rises with the number of opinion
words actually present, so a headline with no signal scores neutral at low
confidence instead of neutral with false certainty.

Entity extraction is NOT re-implemented here — it delegates to
:func:`rugbrain.firehose.quality.extract_entities`, the one extractor the news
channel already uses.
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.firehose.quality import extract_entities

__all__ = ["analyze_item"]

_BULLISH = frozenset(
    {
        "accumulate",
        "adoption",
        "approval",
        "ath",
        "breakout",
        "bullish",
        "buyback",
        "gains",
        "inflow",
        "inflows",
        "jumps",
        "partnership",
        "rally",
        "rallies",
        "rebound",
        "record",
        "soar",
        "soars",
        "surge",
        "surges",
        "upgrade",
    }
)
_BEARISH = frozenset(
    {
        "bankrupt",
        "bearish",
        "breach",
        "charges",
        "crash",
        "delisted",
        "drained",
        "dump",
        "dumps",
        "exploit",
        "fraud",
        "hack",
        "hacked",
        "halt",
        "halted",
        "insolvent",
        "lawsuit",
        "liquidated",
        "liquidation",
        "outflow",
        "outflows",
        "plunge",
        "plunges",
        "rug",
        "rugpull",
        "scam",
        "stolen",
        "vulnerability",
    }
)
#: Majors whose plain name is a ticker in a crypto headline ("bitcoin" → BTC).
_TICKERS = frozenset(
    {
        "ADA",
        "APT",
        "ARB",
        "ATOM",
        "AVAX",
        "BNB",
        "BONK",
        "BTC",
        "DAI",
        "DOGE",
        "DOT",
        "ETH",
        "INJ",
        "JUP",
        "LINK",
        "MATIC",
        "NEAR",
        "OP",
        "PEPE",
        "PYTH",
        "SEI",
        "SHIB",
        "SOL",
        "SUI",
        "TIA",
        "TON",
        "USDC",
        "USDT",
        "WIF",
        "XRP",
    }
)
_WORD_RE = re.compile(r"[a-z']+")
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{2,10})\b")
_ALNUM_RE = re.compile(r"\b[A-Za-z]{2,6}\b")


def analyze_item(text: str, *, title: str = "") -> dict[str, Any]:
    """Score sentiment (-1..1), extract tickers, and reuse the shared entities."""
    body = f"{title} {text or ''}"
    words = _WORD_RE.findall(body.lower())
    bullish = sum(1 for word in words if word in _BULLISH)
    bearish = sum(1 for word in words if word in _BEARISH)
    hits = bullish + bearish
    score = round((bullish - bearish) / hits, 3) if hits else 0.0
    label = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
    confidence = round(min(0.9, 0.3 + 0.1 * hits), 2) if hits else 0.2

    cashtags = {tag.upper() for tag in _CASHTAG_RE.findall(body)}
    named = {word.upper() for word in _ALNUM_RE.findall(body) if word.upper() in _TICKERS}
    return {
        "sentiment": {"label": label, "score": score, "confidence": confidence},
        "tickers": sorted(cashtags | named),
        "entities": extract_entities(body),
    }

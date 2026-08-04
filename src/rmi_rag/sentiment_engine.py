"""Crypto market sentiment engine — text analysis, token aggregation, market mood.

Provides lightweight sentiment scoring without external NLP APIs. Uses
crypto-specific keyword detection, pattern matching, and signal scoring
to produce structured sentiment outputs for tokens and the broader market.

  * ``analyze_text_sentiment(text)``      → {sentiment, confidence, signals}
  * ``analyze_token_sentiment(address)``  → aggregate from social/news/on-chain
  * ``analyze_market_sentiment()``        → overall mood from N sources

All functions are synchronous (no IO), returning results immediately. The
token-level and market-level functions delegate to the RAG collection for
context but degrade gracefully when backends are unavailable.

Usage::

    from rmi_rag.sentiment_engine import analyze_text_sentiment

    result = analyze_text_sentiment("This token is about to rug pull hard")
    # {"sentiment": "bearish", "confidence": 0.82, "signals": ["rug_pull", ...]}

API Endpoints (mounted at /api/v1/sentiment/*)::

    POST /api/v1/sentiment/analyze       — score raw text
    GET  /api/v1/sentiment/token/{addr}  — aggregated token sentiment
    GET  /api/v1/sentiment/market        — overall market mood
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

# ── Router ───────────────────────────────────────────────────────────────────

sentiment_router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])

# ── Configuration ────────────────────────────────────────────────────────────

SENTIMENT_COLLECTION = os.getenv("RAG_SENTIMENT_COLLECTION", "market_sentiment")

# ── Crypto-specific keyword patterns ─────────────────────────────────────────

# Bearish / scam keyword groups with weights (higher weight = stronger signal).
_BEARISH_KEYWORDS: dict[str, float] = {
    "rug": 0.85,
    "rug pull": 0.90,
    "rugpull": 0.90,
    "dump": 0.70,
    "dumping": 0.70,
    "ponzi": 0.95,
    "ponzi scheme": 0.95,
    "scam": 0.85,
    "scammed": 0.85,
    "honeypot": 0.90,
    "hacked": 0.75,
    "exploit": 0.80,
    "exploited": 0.80,
    "drained": 0.80,
    "drainer": 0.85,
    "phishing": 0.85,
    "flash loan": 0.75,
    "flashloan": 0.75,
    "sandwich": 0.65,
    "sandwich attack": 0.70,
    "frontrun": 0.65,
    "frontrunning": 0.65,
    "backdoor": 0.80,
    "backdoored": 0.85,
    "liquidation": 0.50,
    "liquidated": 0.55,
    "depeg": 0.75,
    "depegged": 0.80,
    "bank run": 0.85,
    "insolvent": 0.90,
    "insolvency": 0.90,
    "halt": 0.55,
    "halted": 0.55,
    "paused": 0.55,
    "freeze": 0.60,
    "frozen": 0.60,
    "siphoning": 0.85,
    "siphoned": 0.85,
    "malicious": 0.80,
    "malware": 0.85,
    "wormhole": 0.70,
    "oracle manipulation": 0.80,
    "manipulation": 0.70,
    "manipulated": 0.70,
    "wash trade": 0.75,
    "washed": 0.65,
    "ghosted": 0.70,
    "disappeared": 0.65,
    "exit scam": 0.95,
    "ratted": 0.85,
    "phished": 0.85,
}

# Bullish / positive keyword groups with weights.
_BULLISH_KEYWORDS: dict[str, float] = {
    "moon": 0.55,
    "moonshot": 0.60,
    "bullish": 0.65,
    "bull run": 0.70,
    "bullrun": 0.70,
    "based": 0.50,
    "based af": 0.60,
    "nfa": 0.25,
    "wagmi": 0.55,
    "hodl": 0.40,
    "diamond hands": 0.45,
    "pump": 0.45,
    "pumping": 0.50,
    "parabolic": 0.65,
    "ath": 0.60,
    "all time high": 0.60,
    "breakout": 0.55,
    "sending": 0.45,
    "sent": 0.45,
    "cope": 0.20,
    "ngmi": 0.50,
    "buy the dip": 0.55,
    "btfd": 0.50,
    "accumulation": 0.50,
    "accumulating": 0.50,
    "mainnet": 0.40,
    "tge": 0.40,
    "token launch": 0.35,
    "airdrop": 0.30,
    "airdropped": 0.30,
    "yield": 0.25,
    "staking": 0.30,
    "governance": 0.20,
    "optimistic": 0.55,
    "green": 0.30,
    "legged up": 0.50,
    "rally": 0.50,
    "rerating": 0.55,
}

# Neutral / cautionary keywords.
_NEUTRAL_KEYWORDS: dict[str, float] = {
    "dyor": 0.30,
    "do your own research": 0.30,
    "consolidation": 0.35,
    "consolidating": 0.35,
    "sideways": 0.30,
    "range": 0.20,
    "ranging": 0.25,
    "chop": 0.30,
    "choppy": 0.30,
    "resistance": 0.25,
    "support": 0.25,
    "retracing": 0.35,
    "retrace": 0.35,
    "overbought": 0.40,
    "oversold": 0.40,
    "volatility": 0.30,
    "volatile": 0.30,
}

# Strength modifiers — multiply a keyword's weight by this factor when present.
_STRENGTH_MODIFIERS: dict[str, float] = {
    "definitely": 1.2,
    "certainly": 1.2,
    "absolutely": 1.25,
    "guaranteed": 1.15,
    "confirmed": 1.15,
    "verified": 1.15,
    "official": 1.10,
    "maybe": 0.8,
    "possibly": 0.8,
    "potential": 0.85,
    "alleged": 0.7,
    "allegedly": 0.7,
    "reportedly": 0.8,
    "rumor": 0.65,
    "rumored": 0.65,
    "unconfirmed": 0.6,
    "fud": 0.7,
    "speculation": 0.7,
}

# Exclamation / intensity markers.
_INTENSITY_MARKERS: dict[str, float] = {
    "!!!": 1.15,
    "!!": 1.10,
    "BEWARE": 1.2,
    "WARNING": 1.15,
    "ALERT": 1.15,
    "URGENT": 1.2,
    "🚨": 1.2,
    "⚠️": 1.1,
    "🚀": 1.1,
    "💎": 1.05,
    "📉": 1.1,
    "📈": 1.05,
}

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class SentimentResult:
    """Structured sentiment output from text analysis."""

    sentiment: str  # "bearish", "bullish", "neutral"
    confidence: float  # 0.0-1.0
    signals: list[str]  # matched keywords
    scores: dict[str, float] = field(default_factory=dict)  # bearish/bullish/neutral raw scores
    analyzed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    text_length: int = 0


@dataclass
class TokenSentiment:
    """Aggregated sentiment for a single token."""

    address: str
    chain: str
    sentiment: str
    confidence: float
    signals: list[str]
    source_counts: dict[str, int] = field(default_factory=dict)
    analyzed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class MarketSentiment:
    """Overall crypto market sentiment snapshot."""

    sentiment: str
    confidence: float
    score: float  # -1.0 (extreme fear) to +1.0 (extreme greed)
    sources: int
    top_signals: list[str]
    components: dict[str, Any] = field(default_factory=dict)
    analyzed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ── Keyword extraction ───────────────────────────────────────────────────────


def _extract_keywords(text_lower: str, keyword_map: dict[str, float]) -> tuple[list[str], float]:
    """Find weighted keywords in text. Returns (matched_keys, total_weight)."""
    matched: list[str] = []
    total = 0.0
    # Sort by key length descending so longer phrases match before substrings.
    for keyword in sorted(keyword_map, key=len, reverse=True):
        if keyword in text_lower:
            count = text_lower.count(keyword)
            weight = keyword_map[keyword] * count
            total += weight
            matched.append(keyword)
    return matched, total


def _apply_modifiers(text_raw: str, base_score: float) -> float:
    """Adjust score using strength/intensity modifiers found in text."""
    modified = base_score
    # Strength modifiers
    for modifier, factor in _STRENGTH_MODIFIERS.items():
        if modifier in text_raw.lower():
            modified *= factor
    # Intensity markers
    for marker, factor in _INTENSITY_MARKERS.items():
        if marker in text_raw:
            modified *= factor
    return min(max(modified, 0.0), 1.0)  # Clamp to [0, 1]


# ── Public API ───────────────────────────────────────────────────────────────


def analyze_text_sentiment(text: str) -> SentimentResult:
    """Score the sentiment of *text* using crypto-specific keyword detection.

    Scans for bearish (rug, dump, ponzi, exploit, ...), bullish (moon,
    based, ath, ...), and neutral/cautionary patterns, then combines them
    into a structured ``SentimentResult`` with confidence and signals.

    Returns a ``SentimentResult`` named tuple-like dataclass with
    ``sentiment`` ("bearish"|"bullish"|"neutral"), ``confidence`` (0-1),
    ``signals`` (matched keywords), and ``scores`` (raw category weights).

    >>> analyze_text_sentiment("This token is definitely a rug pull!")
    SentimentResult(sentiment='bearish', confidence=0.9, signals=['rug pull'], ...)
    """
    if not text or not text.strip():
        return SentimentResult(
            sentiment="neutral",
            confidence=0.0,
            signals=[],
            scores={"bearish": 0.0, "bullish": 0.0, "neutral": 0.0},
            text_length=0,
        )

    text_lower = text.lower()
    text_raw = text

    # Extract keyword signals.
    bearish_keys, bearish_raw = _extract_keywords(text_lower, _BEARISH_KEYWORDS)
    bullish_keys, bullish_raw = _extract_keywords(text_lower, _BULLISH_KEYWORDS)
    neutral_keys, neutral_raw = _extract_keywords(text_lower, _NEUTRAL_KEYWORDS)

    # Apply strength/intensity modifiers to each score.
    # Keep raw values for comparison (they carry relative ordering), then
    # clamp the winner to [0,1] for the confidence output.
    bearish_score = _apply_modifiers(text_raw, bearish_raw)
    bullish_score = _apply_modifiers(text_raw, bullish_raw)
    neutral_score = _apply_modifiers(text_raw, neutral_raw)

    # Determine dominant sentiment (compare clamped — but use a higher ceiling
    # so heavily-weighted texts still show the correct winner).
    # We compare *raw* category totals (before clamping) so a heavily-bearish
    # text with many signals still beats a mildly-bullish one.
    if bearish_raw > bullish_raw and bearish_raw > neutral_raw and bearish_raw > 0.1:
        sentiment = "bearish"
        confidence = bearish_score
    elif bullish_raw > bearish_raw and bullish_raw > neutral_raw and bullish_raw > 0.1:
        sentiment = "bullish"
        confidence = bullish_score
    elif neutral_raw > 0.05:
        sentiment = "neutral"
        confidence = neutral_score
    else:
        # No strong signals — lean neutral with low confidence.
        sentiment = "neutral"
        confidence = 0.15

    # Clamp and round scores for output.
    scores = {
        "bearish": round(min(bearish_score, 1.0), 4),
        "bullish": round(min(bullish_score, 1.0), 4),
        "neutral": round(min(neutral_score, 1.0), 4),
    }

    # Collect signals (deduplicated, in order of highest weight).
    all_signals = sorted(
        set(bearish_keys + bullish_keys + neutral_keys),
        key=lambda k: (
            _BEARISH_KEYWORDS.get(k, 0) + _BULLISH_KEYWORDS.get(k, 0) + _NEUTRAL_KEYWORDS.get(k, 0)
        ),
        reverse=True,
    )

    return SentimentResult(
        sentiment=sentiment,
        confidence=round(min(confidence, 1.0), 4),
        signals=all_signals[:20],
        scores=scores,
        text_length=len(text),
    )


async def analyze_token_sentiment(
    address: str,
    chain: str = "ethereum",
    include_social: bool = True,
    include_news: bool = True,
) -> TokenSentiment:
    """Aggregate sentiment for a token from social, news, and on-chain data.

    Queries the RAG collections (threat_intel, news_articles, social_intel)
    for content mentioning *address*, scores each piece with
    ``analyze_text_sentiment``, and returns an aggregated result.

    Degrades gracefully: when backends are unavailable the function
    returns a neutral ``TokenSentiment`` with ``source_counts`` empty.
    """
    all_signals: list[str] = []
    all_confidences: list[float] = []
    source_counts: dict[str, int] = {}
    bearish_score = 0.0
    bullish_score = 0.0
    total_analyzed = 0

    # RAG-style lookup: search each collection for the token address.
    try:
        from rmi_rag.embeddings import get_embedding
        from rmi_rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        embedding = await get_embedding(f"token {address} on {chain}")

        collections_to_check: list[str] = []
        if include_news:
            collections_to_check.append("news_articles")
        if include_social:
            collections_to_check.append("social_intel")
        collections_to_check.append("threat_intel")  # Always check threat intel.

        for col in collections_to_check:
            try:
                results = await store.hybrid_search(
                    query_text=f"token {address} on {chain}",
                    query_embedding=embedding,
                    collection=col,
                    limit=10,
                    min_similarity=0.4,
                )
                analyzed = 0
                for r in results:
                    content = r.get("content", "")
                    if not content:
                        continue
                    sr = analyze_text_sentiment(content)
                    all_signals.extend(sr.signals)
                    all_confidences.append(sr.confidence)
                    bearish_score += sr.scores.get("bearish", 0)
                    bullish_score += sr.scores.get("bullish", 0)
                    analyzed += 1
                source_counts[col] = analyzed
                total_analyzed += analyzed
            except Exception:
                source_counts[col] = 0

        await store.close()
    except Exception as exc:
        logger.warning("analyze_token_sentiment_search_failed address=%s err=%s", address, exc)
        source_counts = {"error": 0}

    # Aggregate.
    if total_analyzed == 0:
        return TokenSentiment(
            address=address,
            chain=chain,
            sentiment="unknown",
            confidence=0.0,
            signals=[],
            source_counts=source_counts,
        )

    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
    avg_bearish = bearish_score / total_analyzed if total_analyzed else 0
    avg_bullish = bullish_score / total_analyzed if total_analyzed else 0

    if avg_bearish > avg_bullish and avg_bearish > 0.1:
        sentiment = "bearish"
        confidence = avg_bearish
    elif avg_bullish > avg_bearish and avg_bullish > 0.1:
        sentiment = "bullish"
        confidence = avg_bullish
    else:
        sentiment = "neutral"
        confidence = avg_confidence

    # Deduplicate and sort signals by frequency.
    signal_counts: dict[str, int] = {}
    for s in all_signals:
        signal_counts[s] = signal_counts.get(s, 0) + 1
    top_signals = sorted(set(all_signals), key=lambda s: signal_counts.get(s, 0), reverse=True)

    return TokenSentiment(
        address=address,
        chain=chain,
        sentiment=sentiment,
        confidence=round(min(confidence, 1.0), 4),
        signals=top_signals[:20],
        source_counts=source_counts,
    )


async def analyze_market_sentiment(
    max_docs: int = 30,
) -> MarketSentiment:
    """Compute an overall crypto market sentiment snapshot.

    Samples recent content from the ``news_articles`` and ``social_intel``
    collections, scores each with ``analyze_text_sentiment``, and returns
    an aggregate ``MarketSentiment`` with a -1.0 (extreme fear) to +1.0
    (extreme greed) score.

    Degrades gracefully: when backends are unavailable the function
    returns a neutral ``MarketSentiment``.
    """
    all_bearish: list[float] = []
    all_bullish: list[float] = []
    all_signals: list[str] = []
    sources = 0

    try:
        from rmi_rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()

        # Scroll recent points from news + social collections.
        for col in ("news_articles", "social_intel"):
            try:
                offset: str | None = None
                collected = 0
                for _ in range(5):  # up to 500 points
                    results, offset = await store.scroll(col, limit=100, offset=offset)
                    if not results:
                        break
                    for r in results:
                        content = r.get("content", "")
                        if not content or len(content) < 50:
                            continue
                        sr = analyze_text_sentiment(content)
                        all_bearish.append(sr.scores.get("bearish", 0))
                        all_bullish.append(sr.scores.get("bullish", 0))
                        all_signals.extend(sr.signals)
                        collected += 1
                        if collected >= max_docs:
                            break
                    if collected >= max_docs or not offset:
                        break
                sources += collected
            except Exception as exc:
                logger.debug("market_sentiment_scroll_failed col=%s err=%s", col, exc)

        await store.close()
    except Exception as exc:
        logger.warning("analyze_market_sentiment_failed: %s", exc)
        sources = 0

    if sources == 0:
        return MarketSentiment(
            sentiment="neutral",
            confidence=0.0,
            score=0.0,
            sources=0,
            top_signals=[],
        )

    # Compute -1.0 to +1.0 score from bearish/bullish averages.
    avg_bearish = sum(all_bearish) / len(all_bearish) if all_bearish else 0.0
    avg_bullish = sum(all_bullish) / len(all_bullish) if all_bullish else 0.0

    # Scale: bearish-heavy → negative, bullish-heavy → positive.
    # Raw scores range 0-1+, but typical values are < 0.3 without strong signals.
    score = round(avg_bullish - avg_bearish, 4)

    if score < -0.15:
        sentiment = "fear"
        confidence = min(avg_bearish, 1.0)
    elif score > 0.15:
        sentiment = "greed"
        confidence = min(avg_bullish, 1.0)
    else:
        sentiment = "neutral"
        confidence = 0.5

    # Deduplicate and sort signals.
    signal_counts: dict[str, int] = {}
    for s in all_signals:
        signal_counts[s] = signal_counts.get(s, 0) + 1
    top_signals = sorted(set(all_signals), key=lambda s: signal_counts.get(s, 0), reverse=True)

    return MarketSentiment(
        sentiment=sentiment,
        confidence=round(confidence, 4),
        score=score,
        sources=sources,
        top_signals=top_signals[:20],
        components={
            "avg_bearish": round(avg_bearish, 4),
            "avg_bullish": round(avg_bullish, 4),
            "docs_analyzed": sources,
            "signal_counts": dict(
                sorted(signal_counts.items(), key=lambda x: x[1], reverse=True)[:15]
            ),
        },
    )


__all__ = [
    "MarketSentiment",
    "SentimentResult",
    "TokenSentiment",
    "analyze_market_sentiment",
    "analyze_text_sentiment",
    "analyze_token_sentiment",
    "sentiment_router",
]


# ── API Endpoints ────────────────────────────────────────────────────────────


@sentiment_router.post("/analyze")
async def sentiment_analyze_endpoint(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    """Analyze the sentiment of raw text (keyword-based, no external APIs).

    Body::

        {"text": "This token is about to rug pull hard, scam alert!"}

    Returns a ``SentimentResult`` as a dict with ``sentiment``, ``confidence``,
    ``signals`` (matched crypto keywords), and raw category ``scores``.
    """
    text = str(data.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail={"error": "text is required"})

    result = analyze_text_sentiment(text)
    return {
        "sentiment": result.sentiment,
        "confidence": result.confidence,
        "signals": result.signals,
        "scores": result.scores,
        "text_length": result.text_length,
        "analyzed_at": result.analyzed_at,
    }


@sentiment_router.get("/token/{address}")
async def sentiment_token_endpoint(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum"),
    include_social: bool = Query(default=True),
    include_news: bool = Query(default=True),
) -> dict[str, Any]:
    """Aggregate sentiment for a token from RAG collections.

    Queries the ``threat_intel``, ``news_articles``, and ``social_intel``
    Qdrant collections for content mentioning *address*, scores each piece,
    and returns an aggregated result::

        curl "http://localhost:8012/api/v1/sentiment/token/0xabc...?chain=ethereum"
    """
    result = await analyze_token_sentiment(
        address=address,
        chain=chain,
        include_social=include_social,
        include_news=include_news,
    )
    return {
        "address": result.address,
        "chain": result.chain,
        "sentiment": result.sentiment,
        "confidence": result.confidence,
        "signals": result.signals,
        "source_counts": result.source_counts,
        "analyzed_at": result.analyzed_at,
    }


@sentiment_router.get("/market")
async def sentiment_market_endpoint(
    request: Request,
    max_docs: int = Query(default=30, ge=10, le=200),
) -> dict[str, Any]:
    """Overall crypto market sentiment snapshot (-1=fear, +1=greed).

    Samples recent content from ``news_articles`` and ``social_intel``
    collections and returns an aggregate with a -1.0 to +1.0 score::

        curl "http://localhost:8012/api/v1/sentiment/market"
    """
    result = await analyze_market_sentiment(max_docs=max_docs)
    return {
        "sentiment": result.sentiment,
        "confidence": result.confidence,
        "score": result.score,
        "sources": result.sources,
        "top_signals": result.top_signals,
        "components": result.components,
        "analyzed_at": result.analyzed_at,
    }

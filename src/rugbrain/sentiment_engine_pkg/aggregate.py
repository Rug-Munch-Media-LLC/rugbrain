"""Token- and market-level aggregation over the RAG collections.

Moved verbatim from ``sentiment_engine.py``. The Qdrant/embedding imports stay
lazy inside each function, exactly as before.
"""

from __future__ import annotations

import logging

from rugbrain.sentiment_engine_pkg.models import MarketSentiment, TokenSentiment
from rugbrain.sentiment_engine_pkg.scoring import analyze_text_sentiment

logger = logging.getLogger(__name__)


async def analyze_token_sentiment(
    address: str,
    chain: str = "ethereum",
    include_social: bool = True,
    include_news: bool = True,
) -> TokenSentiment:
    """Aggregate sentiment for a token from social, news, and on-chain data.

    Queries the RAG collections (threat_intel_v2, news_articles, social_intel)
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
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        embedding = await get_embedding(f"token {address} on {chain}")

        collections_to_check: list[str] = []
        if include_news:
            collections_to_check.append("news_articles")
        if include_social:
            collections_to_check.append("social_intel")
        collections_to_check.append("threat_intel_v2")  # Always check threat intel.

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
        from rugbrain.qdrant_store import QdrantVectorStore

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

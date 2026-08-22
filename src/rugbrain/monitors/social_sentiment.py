"""LunarCrush Social Sentiment Engine — comprehensive crypto social intelligence.

Tracks in real-time:
1. SOCIAL STATS: Per-coin social volume, sentiment, tweets, interactions
2. TRENDING COINS: Top coins by social activity with sentiment analysis
3. SENTIMENT TRENDS: 24h changes, volume spikes, sentiment reversals
4. SOCIAL FEED: Trending posts and discussions across crypto X/Twitter
5. ALERTS: Sentiment extremes, volume spikes, rapid reversals

Endpoints (LunarCrush API v2, free tier):
  GET https://lunarcrush.com/api/v2/public/coins/trending/v2?limit=N&key=KEY
    -> Top coins by social activity with sentiment scores
  GET https://lunarcrush.com/api/v2/public/coin/{symbol}/v2?key=KEY
    -> Specific coin social stats
  GET https://lunarcrush.com/api/v2/public/feed?key=KEY&limit=N
    -> Social feed / trending posts

Rate limits (free tier):
  - 1 request per second
  - Cached in-memory to avoid redundant calls
  - 5-minute background loop polling interval

Outputs stored in-memory for:
  - Dashboard API (via _store dict)
  - Alert dispatch via rugbrain.alert_dispatcher
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

API_KEY = os.getenv("LUNARCRUSH_API_KEY", "")
API_BASE = "https://lunarcrush.com/api/v2"

# Background loop interval (5 minutes default)
POLL_INTERVAL = int(os.getenv("LUNARCRUSH_POLL_INTERVAL", "300"))

# Rate limiter: 1 req/sec for free tier
MIN_REQUEST_GAP = float(os.getenv("LUNARCRUSH_RATE_LIMIT_SEC", "1.0"))

# How many historical data points to keep per symbol for 24h change tracking
HISTORY_WINDOW = int(os.getenv("LUNARCRUSH_HISTORY_WINDOW", "288"))  # 288 * 5min = 24h

# Alert thresholds
SENTIMENT_BULLISH_THRESHOLD = int(os.getenv("LC_SENTIMENT_BULLISH", "80"))
SENTIMENT_BEARISH_THRESHOLD = int(os.getenv("LC_SENTIMENT_BEARISH", "20"))
VOLUME_SPIKE_THRESHOLD = float(os.getenv("LC_VOLUME_SPIKE", "3.0"))  # 3x normal
VOLUME_SPIKE_MILD_THRESHOLD = float(os.getenv("LC_VOLUME_SPIKE_MILD", "2.0"))  # 2x for tracking
SENTIMENT_REVERSAL_THRESHOLD = int(os.getenv("LC_SENTIMENT_REVERSAL", "30"))  # 30+ point change

# Max items per API call
DEFAULT_LIMIT = int(os.getenv("LUNARCRUSH_DEFAULT_LIMIT", "50"))

# ── Rate Limiter ────────────────────────────────────────────────────────────

_last_request_time: float = 0.0
_rate_limit_lock = asyncio.Lock()


async def _throttle() -> None:
    """Enforce rate limit: ensure at least MIN_REQUEST_GAP between requests."""
    global _last_request_time
    async with _rate_limit_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < MIN_REQUEST_GAP:
            await asyncio.sleep(MIN_REQUEST_GAP - elapsed)
        _last_request_time = time.time()


# ── API Client ──────────────────────────────────────────────────────────────


async def _api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Make a rate-limited GET request to LunarCrush API."""
    if not API_KEY:
        logger.debug("lc_api_skipped no_api_key path=%s", path)
        return None

    await _throttle()

    url = f"{API_BASE}{path}"
    headers = {"Accept": "application/json"}
    query_params = dict(params or {})
    query_params.setdefault("key", API_KEY)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=query_params, headers=headers)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                logger.warning(
                    "lc_rate_limited path=%s retry_after=%s",
                    path,
                    r.headers.get("retry-after", "?"),
                )
                return None
            else:
                logger.debug("lc_api_error path=%s status=%d", path, r.status_code)
                return None
    except httpx.TimeoutException:
        logger.debug("lc_api_timeout path=%s", path)
        return None
    except Exception as exc:
        logger.debug("lc_api_failed path=%s err=%s", path, str(exc)[:80])
        return None


# ── In-Memory Stores ────────────────────────────────────────────────────────

# Per-coin social stats: {symbol: {...}}
_coin_stats: dict[str, dict[str, Any]] = {}

# Historical sentiment data for 24h rolling window: {symbol: deque of {timestamp, sentiment}}
_sentiment_history: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=HISTORY_WINDOW)
)

# Historical social volume for spike detection: {symbol: deque of {timestamp, volume}}
_volume_history: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=HISTORY_WINDOW)
)

# Trending coins list (latest snapshot)
_trending_coins: list[dict[str, Any]] = []

# Social feed posts
_social_feed: list[dict[str, Any]] = []

# Alerts generated in the current cycle
_recent_alerts: deque[dict[str, Any]] = deque(maxlen=100)

# Health state
_health_state: dict[str, Any] = {
    "last_successful_poll": 0.0,
    "last_error": "",
    "consecutive_failures": 0,
    "total_polls": 0,
    "coins_tracked": 0,
    "status": "initializing",
}

# Seen post IDs for dedup
_seen_posts: set[str] = set()


# ── Sentiment Classification ────────────────────────────────────────────────


def classify_sentiment(sentiment: float) -> str:
    """Classify a sentiment score into a label.

    Positive > 60, neutral 40-60, negative < 40.
    """
    if sentiment > 60:
        return "positive"
    elif sentiment < 40:
        return "negative"
    return "neutral"


def get_sentiment_label(sentiment: float) -> str:
    """Get a human-readable label for sentiment extremes."""
    if sentiment >= SENTIMENT_BULLISH_THRESHOLD:
        return "🔥 Bullish"
    elif sentiment <= SENTIMENT_BEARISH_THRESHOLD:
        return "💀 Bearish"
    return classify_sentiment(sentiment).capitalize()


def detect_social_volume_spike(
    symbol: str,
    current_volume: int,
) -> dict[str, Any] | None:
    """Detect if current social volume is a spike relative to rolling average.

    Returns spike info dict if volume > VOLUME_SPIKE_MILD_THRESHOLD * rolling avg,
    or None if no spike.
    """
    history = _volume_history.get(symbol)
    if not history or len(history) < 3:
        return None  # Not enough data yet

    # Calculate rolling average (exclude latest)
    volumes = [h["volume"] for h in history]
    if not volumes:
        return None

    rolling_avg = sum(volumes) / len(volumes)
    if rolling_avg <= 0:
        return None

    ratio = current_volume / rolling_avg
    if ratio >= VOLUME_SPIKE_THRESHOLD:
        severity = "critical"
    elif ratio >= VOLUME_SPIKE_MILD_THRESHOLD:
        severity = "high"
    else:
        return None

    return {
        "symbol": symbol,
        "current_volume": current_volume,
        "rolling_avg_volume": round(rolling_avg, 1),
        "ratio": round(ratio, 2),
        "severity": severity,
    }


def detect_sentiment_reversal(symbol: str, current_sentiment: float) -> dict[str, Any] | None:
    """Detect rapid sentiment reversal compared to rolling average.

    Returns reversal info if |current - avg| > SENTIMENT_REVERSAL_THRESHOLD.
    """
    history = _sentiment_history.get(symbol)
    if not history or len(history) < 3:
        return None

    sentiments = [h["sentiment"] for h in history]
    avg_sentiment = sum(sentiments) / len(sentiments)
    change = current_sentiment - avg_sentiment

    if abs(change) >= SENTIMENT_REVERSAL_THRESHOLD:
        direction = "positive" if change > 0 else "negative"
        return {
            "symbol": symbol,
            "previous_avg_sentiment": round(avg_sentiment, 1),
            "current_sentiment": current_sentiment,
            "change": round(change, 1),
            "direction": direction,
            "severity": "high" if abs(change) >= SENTIMENT_REVERSAL_THRESHOLD * 1.5 else "medium",
        }
    return None


def calculate_24h_change(symbol: str) -> dict[str, Any]:
    """Calculate 24h change in sentiment and volume for a symbol."""
    sent_history = list(_sentiment_history.get(symbol, []))
    vol_history = list(_volume_history.get(symbol, []))

    result: dict[str, Any] = {
        "sentiment_change_24h": 0.0,
        "volume_change_24h": 0.0,
        "sentiment_high_24h": 0.0,
        "sentiment_low_24h": 100.0,
        "volume_total_24h": 0,
    }

    if sent_history:
        current = sent_history[-1]["sentiment"] if sent_history else 50.0
        baseline = sent_history[0]["sentiment"] if sent_history else 50.0
        result["sentiment_change_24h"] = round(current - baseline, 1)
        result["sentiment_high_24h"] = max(h["sentiment"] for h in sent_history)
        result["sentiment_low_24h"] = min(h["sentiment"] for h in sent_history)

    if vol_history:
        result["volume_total_24h"] = sum(h["volume"] for h in vol_history)
        if len(vol_history) > 1:
            mid = len(vol_history) // 2
            first_half = sum(h["volume"] for h in vol_history[:mid])
            second_half = sum(h["volume"] for h in vol_history[mid:])
            if first_half > 0:
                result["volume_change_24h"] = round(
                    ((second_half - first_half) / first_half) * 100, 1
                )

    return result


# ── API Fetch Functions ─────────────────────────────────────────────────────


async def fetch_trending_coins(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Fetch trending coins with social stats from LunarCrush.

    GET /public/coins/trending/v2
    Returns: list of {symbol, name, social_score, sentiment, mentions_24h, ...}
    """
    path = "/public/coins/trending/v2"
    data = await _api_get(path, {"limit": min(limit, 100)})
    if not data or not isinstance(data, dict):
        return []

    coins = data.get("data", [])
    if not isinstance(coins, list):
        return []

    results = []
    for coin in coins:
        if not isinstance(coin, dict):
            continue
        symbol = (coin.get("symbol") or "").upper()
        if not symbol:
            continue

        sentiment_raw = coin.get("sentiment")
        # LunarCrush sentiment can be a string ("positive") or a score
        sentiment_score = _parse_sentiment_score(sentiment_raw)

        mentions_24h = int(coin.get("mentions_24h", 0))
        interactions_24h = int(coin.get("interactions_24h", 0))
        contributors_24h = int(coin.get("contributors_24h", 0))

        entry: dict[str, Any] = {
            "symbol": symbol,
            "name": coin.get("name", ""),
            "sentiment_score": sentiment_score,
            "sentiment_label": classify_sentiment(sentiment_score),
            "social_volume": mentions_24h,
            "social_interactions": interactions_24h,
            "social_score": int(coin.get("social_score", 0)),
            "social_dominance": float(coin.get("social_dominance", 0)),
            "contributors_24h": contributors_24h,
            "market_cap": float(coin.get("market_cap", 0)),
            "price": float(coin.get("price", 0)),
            "price_change_24h": float(coin.get("price_change_24h", 0)),
            "galaxy_score": float(coin.get("galaxy_score", 0)),
            "volatility": float(coin.get("volatility", 0)),
            "scraped_at": datetime.now(UTC).isoformat(),
        }

        # Store in per-coin stats
        _coin_stats[symbol] = entry

        # Track history
        now_ts = time.time()
        _sentiment_history[symbol].append(
            {
                "timestamp": now_ts,
                "sentiment": sentiment_score,
            }
        )
        _volume_history[symbol].append(
            {
                "timestamp": now_ts,
                "volume": mentions_24h,
            }
        )

        results.append(entry)

    logger.debug("lc_trending_fetched count=%d limit=%d", len(results), limit)
    return results


async def fetch_coin_social(symbol: str) -> dict[str, Any] | None:
    """Fetch social stats for a specific coin.

    GET /public/coin/{symbol}/v2
    Returns: dict with social stats including sentiment breakdown
    """
    path = f"/public/coin/{symbol}/v2"
    data = await _api_get(path)
    if not data or not isinstance(data, dict):
        return None

    coin_data = data.get("data")
    if isinstance(coin_data, list) and coin_data:
        coin = coin_data[0]
    elif isinstance(coin_data, dict):
        coin = coin_data
    else:
        return None

    if not isinstance(coin, dict):
        return None

    sentiment_raw = coin.get("sentiment")
    sentiment_score = _parse_sentiment_score(sentiment_raw)

    mentions_24h = int(coin.get("mentions_24h", 0))

    result: dict[str, Any] = {
        "symbol": symbol.upper(),
        "name": coin.get("name", ""),
        "sentiment_score": sentiment_score,
        "sentiment_label": classify_sentiment(sentiment_score),
        "social_volume": mentions_24h,
        "social_interactions": int(coin.get("interactions_24h", 0)),
        "social_score": int(coin.get("social_score", 0)),
        "social_dominance": float(coin.get("social_dominance", 0)),
        "contributors_24h": int(coin.get("contributors_24h", 0)),
        "market_cap": float(coin.get("market_cap", 0)),
        "price": float(coin.get("price", 0)),
        "price_change_24h": float(coin.get("price_change_24h", 0)),
        "galaxy_score": float(coin.get("galaxy_score", 0)),
        "volatility": float(coin.get("volatility", 0)),
        "reddit_mentions": int(coin.get("reddit_mentions_24h", 0)),
        "twitter_mentions": int(coin.get("twitter_mentions_24h", 0)),
        "scraped_at": datetime.now(UTC).isoformat(),
    }

    # Update in-memory stores
    _coin_stats[symbol.upper()] = result

    now_ts = time.time()
    _sentiment_history[symbol.upper()].append(
        {
            "timestamp": now_ts,
            "sentiment": sentiment_score,
        }
    )
    _volume_history[symbol.upper()].append(
        {
            "timestamp": now_ts,
            "volume": mentions_24h,
        }
    )

    return result


async def fetch_social_feed(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch social feed / trending posts from LunarCrush.

    GET /public/feed
    Returns: list of posts with content, sentiment, engagement metrics
    """
    path = "/public/feed"
    data = await _api_get(path, {"limit": min(limit, 50)})
    if not data or not isinstance(data, dict):
        return []

    posts = data.get("data", [])
    if not isinstance(posts, list):
        return []

    results = []
    for post in posts:
        if not isinstance(post, dict):
            continue

        post_id = str(post.get("id", "")) or str(post.get("tweet_id", ""))
        if not post_id:
            continue

        # Dedup
        if post_id in _seen_posts:
            continue
        _seen_posts.add(post_id)
        if len(_seen_posts) > 100000:
            _seen_posts.clear()

        sentiment_raw = post.get("sentiment")
        sentiment_score = _parse_sentiment_score(sentiment_raw)

        entry: dict[str, Any] = {
            "id": post_id,
            "author": post.get("author", ""),
            "author_followers": int(post.get("author_followers", 0)),
            "content_preview": (post.get("title", "") or post.get("text", "") or "")[:280],
            "sentiment_score": sentiment_score,
            "sentiment_label": classify_sentiment(sentiment_score),
            "likes": int(post.get("likes", 0)),
            "retweets": int(post.get("retweets", 0)),
            "replies": int(post.get("replies", 0)),
            "engagement_total": int(post.get("likes", 0))
            + int(post.get("retweets", 0))
            + int(post.get("replies", 0)),
            "coin_symbols": post.get("coins", []) or post.get("symbols", []),
            "created_at": post.get("created_at", ""),
            "scraped_at": datetime.now(UTC).isoformat(),
        }
        results.append(entry)

    logger.debug("lc_feed_fetched count=%d", len(results))
    return results


def _parse_sentiment_score(raw: Any) -> float:
    """Parse LunarCrush sentiment into a 0-100 score.

    LunarCrush can return:
    - Numeric score (e.g., 75.5)
    - String label ("positive", "negative", "neutral")
    - None / missing
    """
    if raw is None:
        return 50.0
    if isinstance(raw, (int, float)):
        return max(0.0, min(100.0, float(raw)))
    if isinstance(raw, str):
        mapping = {
            "positive": 80.0,
            "very positive": 90.0,
            "extremely positive": 95.0,
            "neutral": 50.0,
            "negative": 20.0,
            "very negative": 10.0,
            "extremely negative": 5.0,
        }
        return mapping.get(raw.lower().strip(), 50.0)
    return 50.0


# ── Alert Generation ────────────────────────────────────────────────────────


async def _generate_alerts(
    coins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate alerts based on current coin data.

    Checks:
    1. Sentiment extremes (>80 bullish, <20 bearish)
    2. Social volume spikes (3x normal)
    3. Rapid sentiment reversals (30+ point change in 24h)
    """
    alerts: list[dict[str, Any]] = []

    for coin in coins:
        symbol = coin["symbol"]
        sentiment = coin["sentiment_score"]
        volume = coin["social_volume"]

        # 1. Sentiment extremes
        if sentiment >= SENTIMENT_BULLISH_THRESHOLD:
            alerts.append(
                {
                    "type": "sentiment_extreme",
                    "subtype": "bullish",
                    "symbol": symbol,
                    "message": (
                        f"🔥 Extreme bullish sentiment on {symbol}: "
                        f"{sentiment:.1f}/100 (volume: {volume:,} mentions)"
                    ),
                    "value": sentiment,
                    "severity": "medium",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        elif sentiment <= SENTIMENT_BEARISH_THRESHOLD:
            alerts.append(
                {
                    "type": "sentiment_extreme",
                    "subtype": "bearish",
                    "symbol": symbol,
                    "message": (
                        f"💀 Extreme bearish sentiment on {symbol}: "
                        f"{sentiment:.1f}/100 (volume: {volume:,} mentions)"
                    ),
                    "value": sentiment,
                    "severity": "medium",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

        # 2. Social volume spikes
        spike = detect_social_volume_spike(symbol, volume)
        if spike:
            alert_severity = "high" if spike["severity"] == "critical" else "medium"
            alerts.append(
                {
                    "type": "volume_spike",
                    "subtype": "spike",
                    "symbol": symbol,
                    "message": (
                        f"📈 Social volume spike on {symbol}: "
                        f"{volume:,} mentions ({spike['ratio']}x rolling avg)"
                    ),
                    "value": spike["ratio"],
                    "details": spike,
                    "severity": alert_severity,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

        # 3. Sentiment reversals
        reversal = detect_sentiment_reversal(symbol, sentiment)
        if reversal:
            direction_emoji = "🟢" if reversal["direction"] == "positive" else "🔴"
            alerts.append(
                {
                    "type": "sentiment_reversal",
                    "subtype": reversal["direction"],
                    "symbol": symbol,
                    "message": (
                        f"{direction_emoji} Sentiment reversal on {symbol}: "
                        f"{reversal['current_sentiment']:.1f} from {reversal['previous_avg_sentiment']:.1f} "
                        f"({reversal['change']:+.1f} point change)"
                    ),
                    "value": reversal["change"],
                    "details": reversal,
                    "severity": reversal["severity"],
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

    # Dedup by (type, symbol) within window — keep only the strongest
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for alert in sorted(alerts, key=lambda a: abs(a["value"]), reverse=True):
        key = (alert["type"], alert["symbol"])
        if key not in seen:
            seen.add(key)
            deduped.append(alert)

    return deduped


# ── Dispatch Alerts to Telegram ─────────────────────────────────────────────


async def _dispatch_alert(alert: dict[str, Any]) -> None:
    """Dispatch a single alert via the alert_dispatcher if available."""
    try:
        from rugbrain.alert_dispatcher import dispatch_alert

        severity = alert.get("severity", "medium")
        title = alert["message"]
        await dispatch_alert(
            title=title,
            message=(
                f"Type: {alert['type']}\n"
                f"Symbol: {alert['symbol']}\n"
                f"Value: {alert.get('value', '?')}\n"
                f"Severity: {severity}\n"
                f"Source: LunarCrush Social Sentiment"
            ),
            alert_type=f"social_sentiment_{alert['type']}",
            severity=severity,
        )
    except Exception:
        pass  # Alert dispatcher not available or failed


# ── Main Polling Cycle ──────────────────────────────────────────────────────


async def social_sentiment_full_cycle() -> dict[str, Any]:
    """Run one comprehensive LunarCrush social sentiment polling cycle."""
    global _trending_coins, _social_feed

    # Fetch trending coins and social feed in parallel
    trending_coins, feed = await asyncio.gather(
        fetch_trending_coins(limit=DEFAULT_LIMIT),
        fetch_social_feed(limit=20),
    )

    # Update global stores
    _trending_coins = trending_coins
    if feed:
        _social_feed = feed

    # Also fetch detailed stats for top coins (if trending returned any)
    top_symbols = [c["symbol"] for c in trending_coins[:10]]
    if top_symbols:
        detailed_tasks = [fetch_coin_social(sym) for sym in top_symbols]
        await asyncio.gather(*detailed_tasks)
        # Detailed results are merged into _coin_stats via fetch_coin_social

    # Generate alerts from trending coin data
    alerts = await _generate_alerts(trending_coins)

    # Dispatch alerts
    for alert in alerts:
        _recent_alerts.append(alert)
        if alert["severity"] in ("high", "critical"):
            await _dispatch_alert(alert)

    # Compute aggregate stats
    total_vol_24h = sum(c.get("social_volume", 0) for c in trending_coins)
    avg_sentiment = (
        sum(c.get("sentiment_score", 50) for c in trending_coins) / len(trending_coins)
        if trending_coins
        else 50.0
    )

    # Positive / neutral / negative breakdown
    pos_count = sum(1 for c in trending_coins if c.get("sentiment_score", 50) > 60)
    neg_count = sum(1 for c in trending_coins if c.get("sentiment_score", 50) < 40)
    neutral_count = len(trending_coins) - pos_count - neg_count

    # Top movers (by 24h change in social volume)
    top_gainers = sorted(
        [c for c in trending_coins if c.get("price_change_24h", 0) > 0],
        key=lambda c: c.get("price_change_24h", 0),
        reverse=True,
    )[:5]
    top_losers = sorted(
        [c for c in trending_coins if c.get("price_change_24h", 0) < 0],
        key=lambda c: c.get("price_change_24h", 0),
    )[:5]

    # Market cap correlation (simple: avg sentiment of top 10 vs bottom 10 by market cap)
    sorted_by_mcap = sorted(trending_coins, key=lambda c: c.get("market_cap", 0), reverse=True)
    top10_sentiment = (
        sum(c.get("sentiment_score", 50) for c in sorted_by_mcap[:10]) / 10
        if len(sorted_by_mcap) >= 10
        else None
    )
    bottom10_sentiment = (
        sum(c.get("sentiment_score", 50) for c in sorted_by_mcap[-10:]) / 10
        if len(sorted_by_mcap) >= 10
        else None
    )

    # Update health
    _health_state["last_successful_poll"] = time.time()
    _health_state["consecutive_failures"] = 0
    _health_state["coins_tracked"] = len(_coin_stats)
    _health_state["total_polls"] += 1
    _health_state["status"] = "running"

    cycle_result: dict[str, Any] = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "coins_tracked": len(trending_coins),
        "coins_in_history": len(_coin_stats),
        "total_social_volume_24h": total_vol_24h,
        "avg_sentiment": round(avg_sentiment, 1),
        "sentiment_breakdown": {
            "positive": pos_count,
            "neutral": neutral_count,
            "negative": neg_count,
            "bullish_extreme": sum(
                1
                for c in trending_coins
                if c.get("sentiment_score", 50) >= SENTIMENT_BULLISH_THRESHOLD
            ),
            "bearish_extreme": sum(
                1
                for c in trending_coins
                if c.get("sentiment_score", 50) <= SENTIMENT_BEARISH_THRESHOLD
            ),
        },
        "market_cap_correlation": {
            "top10_avg_sentiment": round(top10_sentiment, 1) if top10_sentiment else None,
            "bottom10_avg_sentiment": round(bottom10_sentiment, 1) if bottom10_sentiment else None,
            "sentiment_gap": (
                round(top10_sentiment - bottom10_sentiment, 1)
                if top10_sentiment is not None and bottom10_sentiment is not None
                else None
            ),
        },
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "alerts_generated": len(alerts),
        "alerts": alerts[:10],
        "feed_posts": len(_social_feed),
    }

    logger.info(
        "lc_cycle coins=%d total_vol=%d avg_sent=%.1f alerts=%d",
        len(trending_coins),
        total_vol_24h,
        avg_sentiment,
        len(alerts),
    )

    return cycle_result


# ── Health Check ────────────────────────────────────────────────────────────


async def social_sentiment_health() -> dict[str, Any]:
    """Return health check data for the LunarCrush social sentiment engine."""
    now = time.time()
    last_poll = _health_state.get("last_successful_poll", 0)
    time_since_poll = now - last_poll

    ok = (
        API_KEY != ""
        and _health_state.get("status") == "running"
        and time_since_poll < POLL_INTERVAL * 3  # Allow 3 missed intervals
    )

    return {
        "name": "lunarcrush_social_sentiment",
        "ok": ok,
        "status": _health_state.get("status", "unknown"),
        "api_key_configured": API_KEY != "",
        "last_poll_ts": last_poll,
        "time_since_last_poll_s": round(time_since_poll, 1),
        "consecutive_failures": _health_state.get("consecutive_failures", 0),
        "total_polls": _health_state.get("total_polls", 0),
        "coins_tracked": len(_coin_stats),
        "coins_in_history": len(_sentiment_history),
        "feed_posts_cached": len(_social_feed),
        "recent_alerts": len(_recent_alerts),
        "poll_interval_s": POLL_INTERVAL,
    }


# ── Dashboard Data ──────────────────────────────────────────────────────────


def get_social_sentiment_state() -> dict[str, Any]:
    """Return full social sentiment state for dashboard API consumption."""
    # Build per-coin 24h changes
    coin_details = {}
    for symbol, stats in _coin_stats.items():
        change = calculate_24h_change(symbol)
        coin_details[symbol] = {
            **stats,
            **change,
            "sentiment_trend": get_sentiment_label(stats.get("sentiment_score", 50)),
        }

    # Top by social volume
    top_by_volume = sorted(
        coin_details.values(),
        key=lambda c: c.get("social_volume", 0),
        reverse=True,
    )[:20]

    # Top by sentiment (most positive)
    top_by_sentiment = sorted(
        coin_details.values(),
        key=lambda c: c.get("sentiment_score", 50),
        reverse=True,
    )[:10]

    # Bottom by sentiment (most negative)
    bottom_by_sentiment = sorted(
        coin_details.values(),
        key=lambda c: c.get("sentiment_score", 50),
    )[:10]

    # Social volume spikes detected
    spikes = []
    for symbol, stats in _coin_stats.items():
        vol = stats.get("social_volume", 0)
        spike = detect_social_volume_spike(symbol, vol)
        if spike:
            spikes.append(spike)

    return {
        "last_updated": datetime.now(UTC).isoformat(),
        "health": _health_state,
        "summary": {
            "coins_tracked": len(_coin_stats),
            "total_volume_24h": sum(c.get("social_volume", 0) for c in _coin_stats.values()),
            "avg_sentiment": (
                round(
                    sum(c.get("sentiment_score", 50) for c in _coin_stats.values())
                    / len(_coin_stats),
                    1,
                )
                if _coin_stats
                else None
            ),
        },
        "top_by_volume": top_by_volume,
        "top_by_sentiment": top_by_sentiment,
        "bottom_by_sentiment": bottom_by_sentiment,
        "volume_spikes": spikes[:10],
        "recent_alerts": list(_recent_alerts)[-20:],
        "trending_coins": _trending_coins[:20],
        "social_feed": _social_feed[:20],
        "coin_details": coin_details,
    }


# ── Background Loop ─────────────────────────────────────────────────────────


async def social_sentiment_loop() -> None:
    """Run LunarCrush social sentiment monitoring in a continuous background loop.

    Polls every POLL_INTERVAL seconds (default: 300 = 5 minutes).
    Imports cleanly as::

        from rugbrain.monitors.social_sentiment import social_sentiment_loop
    """
    logger.info("lc_monitor_started interval=%ds", POLL_INTERVAL)

    if not API_KEY:
        logger.warning("lc_monitor_no_api_key LUNARCRUSH_API_KEY not set — monitoring disabled")
        _health_state["status"] = "disabled"
        # Still run the loop but it'll log a warning each cycle
        # Allows graceful handling if key is added at runtime

    while True:
        try:
            result = await social_sentiment_full_cycle()
            if result["alerts_generated"] > 0:
                logger.info(
                    "lc_alerts count=%d extremes=%s spikes=%s reversals=%s",
                    result["alerts_generated"],
                    result["sentiment_breakdown"].get("bullish_extreme", 0),
                    sum(1 for a in result["alerts"] if a.get("type") == "volume_spike"),
                    sum(1 for a in result["alerts"] if a.get("type") == "sentiment_reversal"),
                )
        except Exception as exc:
            _health_state["consecutive_failures"] += 1
            _health_state["last_error"] = str(exc)[:200]
            logger.warning(
                "lc_cycle_failed attempt=%d err=%s",
                _health_state["consecutive_failures"],
                str(exc)[:80],
            )

        await asyncio.sleep(POLL_INTERVAL)

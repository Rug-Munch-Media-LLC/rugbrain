"""Generate alerts from current coin social data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


async def _generate_alerts(
    coins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate alerts based on current coin data.

    Checks:
    1. Sentiment extremes (>80 bullish, <20 bearish)
    2. Social volume spikes (3x normal)
    3. Rapid sentiment reversals (30+ point change in 24h)
    """
    from rugbrain.monitors import social_sentiment as _pkg

    alerts: list[dict[str, Any]] = []

    for coin in coins:
        symbol = coin["symbol"]
        sentiment = coin["sentiment_score"]
        volume = coin["social_volume"]

        # 1. Sentiment extremes
        if sentiment >= _pkg.SENTIMENT_BULLISH_THRESHOLD:
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
        elif sentiment <= _pkg.SENTIMENT_BEARISH_THRESHOLD:
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
        spike = _pkg.detect_social_volume_spike(symbol, volume)
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
        reversal = _pkg.detect_sentiment_reversal(symbol, sentiment)
        if reversal:
            direction_emoji = "🟢" if reversal["direction"] == "positive" else "🔴"
            alerts.append(
                {
                    "type": "sentiment_reversal",
                    "subtype": reversal["direction"],
                    "symbol": symbol,
                    "message": (
                        f"{direction_emoji} Sentiment reversal on {symbol}: "
                        f"{reversal['current_sentiment']:.1f} from "
                        f"{reversal['previous_avg_sentiment']:.1f} "
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

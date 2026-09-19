"""Price bands and severity classification for liquidation events."""

from __future__ import annotations

from typing import Any


def classify_severity(usd_value: float) -> str:
    """Classify liquidation severity based on USD value.

    Args:
        usd_value: The USD value of the liquidation.

    Returns:
        Severity string: "critical", "high", or "medium".
    """
    if usd_value > 10_000_000:
        return "critical"
    if usd_value > 1_000_000:
        return "high"
    return "medium"


def classify_hype_level(usd_value: float) -> str | None:
    """Classify hype level for a liquidation event.

    Args:
        usd_value: The USD value of the liquidation.

    Returns:
        Hype level string or None if not hype-worthy.
    """
    if usd_value > 1_000_000:
        return "MEGA LIQUIDATION"
    if usd_value > 500_000:
        return "LARGE LIQUIDATION"
    if usd_value > 100_000:
        return "NOTABLE LIQUIDATION"
    return None


def calculate_liquidator_profit(usd_value: float) -> float:
    """Estimate liquidator profit from a liquidation.

    Args:
        usd_value: The USD value of the liquidation.

    Returns:
        Estimated profit (~5% of liquidation value).
    """
    return usd_value * 0.05


def classify_win_level(estimated_profit: float) -> str | None:
    """Classify liquidation win level based on estimated profit.

    Args:
        estimated_profit: The estimated liquidator profit.

    Returns:
        Win level string: "PROFITABLE", "GOOD", "DECENT", or None.
    """
    if estimated_profit >= 50_000:
        return "PROFITABLE"
    if estimated_profit >= 10_000:
        return "GOOD"
    if estimated_profit >= 5_000:
        return "DECENT"
    return None


def get_hype_liquidations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify hype liquidations — large positions with social media mentions.

    Flags: >$1M single position, >$100k on meme coins, liquidator profit >$50k.

    Args:
        events: List of liquidation event dictionaries.

    Returns:
        List of events with hype_level field added.
    """
    hype = []
    for e in events:
        usd = e.get("value_usd", 0)
        level = classify_hype_level(usd)
        if level:
            e["hype_level"] = level
            hype.append(e)
    return hype


def get_liquidation_wins(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify liquidation wins — profitable liquidator opportunities.

    Flags: positions where liquidator could profit from the spread.

    Args:
        events: List of liquidation event dictionaries.

    Returns:
        List of events with estimated_liquidator_profit and win_level fields.
    """
    wins = []
    for e in events:
        usd = e.get("value_usd", 0)
        if usd > 0:
            estimated_profit = calculate_liquidator_profit(usd)
            if estimated_profit >= 5_000:
                wins.append(
                    {
                        **e,
                        "estimated_liquidator_profit": round(estimated_profit, 2),
                        "win_level": classify_win_level(estimated_profit),
                    }
                )
    return wins

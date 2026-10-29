"""Polymarket market dict -> corpus ``IngestDoc`` (one doc per market).

The stored text is written to be retrievable by a natural question
("odds of a SOL ETF approval", "who leads the 2028 field"): the question is
repeated verbatim, every outcome carries its probability, and the resolution
date, volume and category are stated in words rather than left in payload-only
fields. Provenance (slug, Polymarket URL) and ``fetched_at`` travel in
``metadata`` so the answer path can apply the freshness contract.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.prediction_markets_freshness import freshness

SOURCE = "polymarket_live"
DOC_TYPE = "prediction_market"
COLLECTION = "rag_documents"
MARKET_URL = "https://polymarket.com/market/{slug}"


def _outcomes(market: dict[str, Any]) -> list[tuple[str, float | None]]:
    """Gamma returns ``outcomes``/``outcomePrices`` as JSON-encoded strings."""
    raw = market.get("outcomes")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    rows: list[tuple[str, float | None]] = []
    if not isinstance(raw, list):
        return rows
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("outcome") or "").strip()
            price = item.get("price", item.get("probability"))
        else:
            name = str(item).strip()
            price = None
        if price is None and isinstance(prices, list) and index < len(prices):
            price = prices[index]
        try:
            value = float(price) if price is not None else None
        except (TypeError, ValueError):
            value = None
        if name:
            rows.append((name, value))
    return rows


def market_to_doc(
    market: dict[str, Any],
    fetched_at: datetime | str,
    now: datetime | None = None,
) -> IngestDoc | None:
    """Primary entry point. ``None`` for an unusable market (no question/slug)."""
    question = str(market.get("question") or "").strip()
    slug = str(market.get("slug") or market.get("id") or "").strip()
    if not question or not slug:
        return None

    state = freshness(fetched_at, now=now)
    outcomes = _outcomes(market)
    odds_line = " / ".join(
        f"{name} {price * 100:.1f}%" if isinstance(price, float) else name for name, price in outcomes
    ) or "odds unavailable"
    volume = market.get("volume24hr", market.get("volume"))
    end_date = market.get("end_date") or market.get("endDate") or "unpublished"
    category = str(market.get("category") or market.get("tag") or "uncategorized").strip()
    url = MARKET_URL.format(slug=slug)

    label = "STALE" if state["stale"] else "CURRENT"
    content = (
        f"Prediction market: {question}\n"
        f"Odds: {odds_line}\n"
        f"24h volume: {volume if volume is not None else 'unknown'}"
        f" | liquidity: {market.get('liquidity', 'unknown')}\n"
        f"Resolves: {end_date}\n"
        f"Category: {category}\n"
        f"Exchange: Polymarket | Market: {slug}\n"
        f"Odds snapshot [{label}] fetched {state['fetched_at']} "
        f"({state['age_hours']}h old, stale after {state['stale_after_hours']}h)\n"
        f"Source: {url}"
    )

    metadata: dict[str, Any] = {
        "question": question,
        "slug": slug,
        "market_id": market.get("id"),
        "condition_id": market.get("condition_id"),
        "category": category,
        "exchange": "polymarket",
        "odds": dict(outcomes),
        "probabilities": {name: round(price * 100, 2) for name, price in outcomes if price is not None},
        "volume_24h": volume,
        "liquidity": market.get("liquidity"),
        "resolution_date": end_date,
        "fetched_at": state["fetched_at"],
        "odds_age_hours": state["age_hours"],
        "odds_freshness": state["label"],
        "stale_after_hours": state["stale_after_hours"],
        "source_url": url,
        "knowledge_type": "prediction_market",
    }
    return IngestDoc(
        content=content,
        doc_id=f"{SOURCE}:{slug}",
        metadata=metadata,
        doc_type=DOC_TYPE,
        source=SOURCE,
        title=question,
        url=url,
    )


__all__ = ["COLLECTION", "DOC_TYPE", "SOURCE", "market_to_doc"]

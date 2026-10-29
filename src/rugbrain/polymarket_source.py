"""Polymarket reads — MCP where strictly better, gamma for the bulk slice.

Routing (one decision, written once):

  * odds snapshot / natural-language search -> MCP ``polymarket_market_snapshot``
    then ``polymarket_search``                      (falls back to gamma)
  * order-book depth / price history        -> MCP only
    (:mod:`rugbrain.polymarket_mcp`)
  * bulk 50-market slice                    -> gamma HTTP (the MCP cannot page)

Every read is citation-safe: ``url`` comes from
:func:`rugbrain.polymarket_ref.ref_url`, never from a local string literal.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rugbrain import polymarket_ref
from rugbrain.polymarket_mcp import http_mcp_call

log = logging.getLogger(__name__)

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
MAX_AGE = timedelta(hours=6)
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
MarketCall = Callable[[str, dict[str, Any]], dict[str, Any]]


class StaleMarketError(RuntimeError):
    """Market data is older than the 6 h freshness budget."""


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _yes_price(raw: dict[str, Any]) -> float | None:
    for key in ("yes_price", "midpoint", "probability", "lastTradePrice"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    prices = raw.get("outcomePrices") or raw.get("outcome_prices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            return None
    if isinstance(prices, list) and prices:
        try:
            return float(prices[0])
        except (TypeError, ValueError):
            return None
    return None


def gamma_market(query: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Bulk gamma slice filtered locally — the MCP cannot page 50 markets."""
    params = {"active": "true", "closed": "false", "limit": 100}
    if client is None:
        with httpx.Client(timeout=TIMEOUT) as owned:
            resp = owned.get(GAMMA_MARKETS, params=params)
    else:
        resp = client.get(GAMMA_MARKETS, params=params)
    resp.raise_for_status()
    rows = resp.json() or []
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("markets") or []
    tokens = [token for token in query.lower().split() if token]
    best: dict[str, Any] = {}
    best_score = 0
    for market in rows:
        question = str(market.get("question") or market.get("title") or "").lower()
        score = sum(1 for token in tokens if token in question)
        if score > best_score:
            best, best_score = market, score
    if not best and rows:  # no token hit: highest-volume active market
        best = max(rows, key=lambda m: float(m.get("volumeNum") or m.get("volume") or 0))
    return best


def _mcp_market(call: MarketCall, query: str) -> dict[str, Any]:
    """Snapshot first (one call), then search (ranked candidates)."""
    snap = call("polymarket_market_snapshot", {"query": query})
    if isinstance(snap, dict):
        candidate = snap.get("market") if isinstance(snap.get("market"), dict) else snap
        if isinstance(candidate, dict) and candidate.get("slug"):
            return candidate
    found = call("polymarket_search", {"query": query, "limit_per_type": 3})
    for market in found.get("markets") or found.get("result") or []:
        if isinstance(market, dict) and market.get("slug"):
            return market
    return {}


def _shape(raw: dict[str, Any], path: str, now: datetime) -> dict[str, Any]:
    slug = str(raw.get("slug") or "").strip()
    if not slug:
        raise LookupError("polymarket market has no slug")
    probability = _yes_price(raw)
    if probability is None:
        raise LookupError(f"no YES price for {slug}")
    seen = _parse_ts(raw.get("fetched_at") or raw.get("updatedAt") or raw.get("updated_at"))
    age = (now - seen) if seen else None
    if age is not None and age > MAX_AGE:
        raise StaleMarketError(f"{slug}: data {age} old (budget {MAX_AGE})")
    volume = raw.get("volume") or raw.get("volumeNum") or raw.get("volume_usd")
    try:
        volume_usd = float(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume_usd = None
    return {
        "title": str(raw.get("question") or raw.get("title") or slug),
        "slug": slug,
        "implied_pct": round(probability * 100, 1),
        "volume_usd": volume_usd,
        "resolution_date": raw.get("endDate") or raw.get("end_date") or raw.get("resolution_date"),
        "freshness": {
            "fetched_at": (seen or now).isoformat(),
            "age_seconds": int(age.total_seconds()) if age else 0,
            "budget_hours": 6,
        },
        "url": polymarket_ref.ref_url(slug),
        "path": path,
    }


def market_odds(
    query: str,
    *,
    mcp: MarketCall | None = None,
    gamma: Callable[[str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve one market's odds: MCP first, gamma only when the MCP is down."""
    moment = now or datetime.now(UTC)
    raw: dict[str, Any] = {}
    path = "mcp"
    try:
        raw = _mcp_market(mcp or http_mcp_call, query)
    except Exception as exc:  # MCP unavailable/degraded -> gamma fallback
        log.warning("polymarket mcp unavailable (%s) — gamma fallback", exc)
    if not raw:
        path, raw = "gamma", (gamma or gamma_market)(query)
    if not raw:
        raise LookupError(f"no polymarket market matched {query!r}")
    return _shape(raw, path, moment)


def citation_line(market: dict[str, Any]) -> str:
    """One-line citation: title, implied %, volume, resolution, freshness, URL."""
    raw_volume = market.get("volume_usd")
    volume = f"${raw_volume:,.0f}" if raw_volume is not None else "n/a"
    return (
        f"{market['title']} — implied {market['implied_pct']}% YES, 24h volume {volume}, "
        f"resolves {market.get('resolution_date')}, "
        f"{market['freshness']['age_seconds']}s old ({market['path']}). {market['url']}"
    )

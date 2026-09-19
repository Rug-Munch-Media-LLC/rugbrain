"""Remote component fetchers for the crypto fear index.

Every fetcher fails OPEN: any error (or missing response field) is logged
and converted to ``None`` so the composite renormalizes over the remaining
components instead of raising into the API layer.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("rugbrain.fear_index")


async def fetch_fng(client: httpx.AsyncClient) -> int | None:
    """Alternative.me Fear & Greed value (0-100), None on any failure."""
    from rugbrain import fear_index as _pkg

    try:
        r = await client.get(_pkg.FNG_URL, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        values = data.get("data") or []
        if not values:
            return None
        return _pkg._clamp(float(values[0].get("value") or 50))
    except Exception as e:
        log.warning("fear_fng_error: %s", e)
        return None


async def fetch_social_sentiment(client: httpx.AsyncClient, query: str | None = None) -> int | None:
    """Crypto social sentiment (0-100) from SearXNG JSON results.

    Scores each result title/description against positive/negative lexicons;
    neutral text counts toward neither side. Returns None when SearXNG is
    unreachable or returns no results.
    """
    from rugbrain import fear_index as _pkg

    try:
        r = await client.get(
            _pkg.SEARXNG_URL,
            params={"q": query or _pkg.SEARXNG_QUERY, "format": "json"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        results = data.get("results") or []
        if not results:
            return None
        texts: list[str] = []
        for res in results:
            if not isinstance(res, dict):
                continue
            title = str(res.get("title") or "")
            content = str(res.get("content") or "")
            if title or content:
                texts.append(f"{title} {content}")
        if not texts:
            return None
        positive = sum(1 for text in texts if _pkg._term_hit(text, _pkg.POSITIVE_TERMS))
        negative = sum(1 for text in texts if _pkg._term_hit(text, _pkg.NEGATIVE_TERMS))
        return _pkg._clamp(50 + (positive - negative) / len(texts) * 50)
    except Exception as e:
        log.warning("fear_social_error: %s", e)
        return None


def _onchain_component(raw: Any) -> int | None:
    """Normalize a -100..100 on-chain signal to a 0-100 greed scale."""
    from rugbrain import fear_index as _pkg

    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return _pkg._clamp((float(raw) + 100) / 2)


async def fetch_onchain_signals(client: httpx.AsyncClient) -> int | None:
    """On-chain exchange flows / whales / liquidations (0-100 greed scale).

    Reads ``ONCHAIN_SIGNALS_URL`` (a DataBus-style provider) with fields:
    ``exchange_netflow`` (-100..100, positive = inflows = fear),
    ``whale_movements`` (-100..100) and ``liquidations`` (-100..100,
    positive = high liquidations = fear). Returns None when unconfigured.
    """
    from rugbrain import fear_index as _pkg

    if not _pkg.ONCHAIN_SIGNALS_URL:
        return None
    try:
        r = await client.get(_pkg.ONCHAIN_SIGNALS_URL, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        netflow = _onchain_component(data.get("exchange_netflow"))
        whales = _onchain_component(data.get("whale_movements"))
        liquidations = _onchain_component(data.get("liquidations"))
        # Inflows and liquidations are fear signals → invert to greed scale.
        if netflow is not None:
            netflow = 100 - netflow
        if liquidations is not None:
            liquidations = 100 - liquidations
        available = [v for v in (netflow, whales, liquidations) if v is not None]
        if not available:
            return None
        return round(sum(available) / len(available))
    except Exception as e:
        log.warning("fear_onchain_error: %s", e)
        return None

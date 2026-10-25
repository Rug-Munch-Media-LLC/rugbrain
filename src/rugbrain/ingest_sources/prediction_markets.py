"""Prediction markets — bounded live Polymarket slice (top active by 24h volume).

Source: https://gamma-api.polymarket.com/markets (public, no credentials).
The slice is deliberately bounded (default 50, hard cap 200): this is a
*corpus seed* so odds become searchable, not a market-data feed. Each market
becomes one ``IngestDoc`` via ``rugbrain.prediction_markets_docs``, carrying
``fetched_at`` and the freshness contract from
``rugbrain.prediction_markets_freshness``.

Fail-open (per ``base.py``): any fetch/parse error logs and returns ``[]``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.prediction_markets_docs import COLLECTION, DOC_TYPE, SOURCE as SOURCE_NAME, market_to_doc

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_LIMIT = 50
HARD_CAP = 200


class PredictionMarkets(Source):
    """Top active Polymarket markets, ordered by 24h volume."""

    name = SOURCE_NAME
    doc_type = DOC_TYPE
    default_collection = COLLECTION
    description = "Bounded live Polymarket slice (active markets, 24h volume order)"

    async def _fetch(self, client: httpx.AsyncClient, limit: int) -> list[IngestDoc]:
        """Primary entry point: fetch the slice and normalise it into docs."""
        bound = max(1, min(int(limit or DEFAULT_LIMIT), HARD_CAP))
        params: dict[str, Any] = {
            "active": "true",
            "closed": "false",
            "limit": bound,
            "order": "volume24hr",
            "ascending": "false",
        }
        try:
            response = await client.get(GAMMA_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - fail-open by contract
            log.warning("polymarket fetch failed: %s", exc)
            return []

        markets = payload if isinstance(payload, list) else payload.get("data", [])
        if not isinstance(markets, list):
            log.warning("polymarket payload not a list: %r", type(markets))
            return []

        fetched_at = datetime.now(timezone.utc)
        docs = [d for d in (market_to_doc(m, fetched_at) for m in markets if isinstance(m, dict)) if d]
        log.info("polymarket: %d markets -> %d docs (bound=%d)", len(markets), len(docs), bound)
        return docs


SOURCE = PredictionMarkets()

__all__ = ["PredictionMarkets", "SOURCE", "GAMMA_URL", "DEFAULT_LIMIT", "HARD_CAP"]
